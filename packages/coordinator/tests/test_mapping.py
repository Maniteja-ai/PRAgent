import copy
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from trace_coordinator.application.mapping import (
    MappingConfig,
    project_snapshot,
    validate_mappings,
    verified_bytes,
)
from trace_coordinator.application.ui_evidence import ui_index
from trace_coordinator.domain.project import ApplicationConfig
from trace_coordinator.infrastructure.artifacts import save_artifact
from trace_coordinator.tool.implementations import knowledge

ROOT = Path(__file__).resolve().parents[1]
REV = "b" * 40
SOURCE = 'export const Checkout = () => (\n<input placeholder="Discount code" />\n);\n'
PATCH = (
    "diff --git a/src/view.tsx b/src/view.tsx\n--- a/src/view.tsx\n+++ b/src/view.tsx\n@@ -1,1 +1,3 @@\n-old\n+"
    + SOURCE.replace("\n", "\n+").rstrip("+")
)


class Inspector:
    def inspect(self, candidate, element):
        return {
            "owner": "Checkout",
            "owner_start_line": 1,
            "source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
        }


def rebuild(report):
    report["ui_knowledge"] = ui_index(report["evidence"])


@pytest.fixture
def batch(tmp_path):
    def artifact(data, suffix):
        return save_artifact(
            tmp_path, "input", data if isinstance(data, bytes) else json.dumps(data).encode(), suffix
        )

    def screen(ref, elements, transition=None):
        observation = dict(
            environment="patched",
            claimed_revision=REV,
            url="https://patched.example/checkout",
            elements=elements,
        )
        metadata = dict(
            environment="patched",
            observation=artifact(observation, ".observation.json"),
            dom=artifact(b"<input />", ".html"),
            screenshot=artifact(b"png", ".png"),
        )
        if transition:
            metadata.update(transition_record=transition, transition=artifact(transition, ".transition.json"))
        return dict(
            id=ref,
            project_id="project",
            kind="browser",
            source=observation["url"],
            summary=json.dumps(observation),
            metadata=metadata,
        )

    transition = dict(
        environment="patched",
        **{"from": "start", "to": "end"},
        action="click",
        tool="browser.act",
        element_id="go",
        element_name="Checkout",
    )
    evidence = {
        "diff": dict(
            id="diff",
            project_id="project",
            kind="diff",
            source="local-git",
            summary=PATCH,
            metadata=dict(
                artifact=artifact(PATCH.encode(), ".diff"),
                changes=dict(analysis_base="a" * 40, analysis_head=REV, files=[{"path": "src/view.tsx"}]),
            ),
        ),
        "start": screen("start", [{"id": "go", "name": "Checkout", "tag": "button", "type": "button"}]),
        "end": screen(
            "end", [{"id": "field", "name": "Discount code", "tag": "input", "type": ""}], transition
        ),
    }
    report = dict(evidence=evidence)
    rebuild(report)
    app = ApplicationConfig(
        project_id="project",
        repository="owner/repo",
        repository_path=str(tmp_path),
        graph_snapshot_file="baseline.json",
        ingestion_run_directory="runs",
        retrieval_config_file="retrieval.json",
        vector_directory="vector",
        baseline=dict(url="https://baseline.example", revision="a" * 40),
        patched=dict(url="https://patched.example", revision=REV),
    )
    config = MappingConfig(
        application_file=str(tmp_path / "app.json"),
        report_file=str(tmp_path / "report.json"),
        report_sha256="a" * 64,
        code_config_file=str(tmp_path / "code.json"),
        env_file=str(tmp_path / ".env"),
        output_directory=str(tmp_path / "output"),
        flow_name="Open checkout",
    )
    code = dict(
        id="code",
        origin="test",
        diagnostics=[],
        edges=[],
        nodes=[
            dict(
                id="owner",
                kind="CodeSymbol",
                name="Checkout",
                project_id="project",
                revision=REV,
                properties=dict(
                    path="src/view.tsx",
                    start_line=1,
                    end_line=3,
                    sha256=hashlib.sha256(SOURCE.encode()).hexdigest(),
                ),
            )
        ],
    )
    return report, config, app, code


def test_validated_projection_is_immutable_deterministic_and_has_no_behavior_or_requirements(batch):
    report, config, app, code = batch
    original = copy.deepcopy(code)
    mappings = validate_mappings(report, config, app, Inspector())
    graph = project_snapshot(code, mappings, config, app.project_id, REV)
    assert graph == project_snapshot(code, mappings, config, app.project_id, REV)
    assert code == original
    assert len(graph["nodes"]) == 3
    assert {e["type"] for e in graph["edges"]} == {"RENDERS", "CONTAINS"}
    assert all(
        e["status"] == "CONFIRMED" and not e["properties"]["runtime_attribution_verified"]
        for e in graph["edges"]
    )
    assert mappings[0]["behavior_verification"] == "NOT_RUN"
    from trace_impact.shared.graph_models import GraphSnapshot

    GraphSnapshot.model_validate(graph)


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("project", "project mismatch"),
        ("index", "underlying evidence"),
        ("missing", "No complete"),
        ("hash", "hash/size"),
        ("summary", "differs from saved"),
        ("revision", "scope mismatch"),
        ("transition", "Transition differs"),
        ("candidate_revision", "Candidate revision"),
        ("duplicate", "Ambiguous source"),
        ("label", "ambiguous or mismatched"),
        ("flow", "terminal flow"),
        ("action", "not supported"),
    ],
)
def test_evidence_failures_block_entire_batch(batch, mutation, message):
    report, config, app, _ = batch
    end = report["evidence"]["end"]
    if mutation == "project":
        end["project_id"] = "other"
    elif mutation == "index":
        report["ui_knowledge"]["code_ui_candidates"][0]["line"] = 99
    elif mutation == "missing":
        report["evidence"].pop("diff")
        rebuild(report)
    elif mutation == "hash":
        end["metadata"]["dom"]["sha256"] = "f" * 64
    elif mutation == "summary":
        data = json.loads(end["summary"])
        data["text"] = "changed"
        end["summary"] = json.dumps(data)
        rebuild(report)
    elif mutation == "revision":
        app = app.model_copy(update={"patched": app.patched.model_copy(update={"revision": "c" * 40})})
    elif mutation == "transition":
        end["metadata"]["transition_record"]["action"] = "fill"
        rebuild(report)
    elif mutation == "candidate_revision":
        report["evidence"]["diff"]["metadata"]["changes"]["analysis_head"] = "c" * 40
        rebuild(report)
    elif mutation == "duplicate":
        report["evidence"]["diff"]["summary"] += '+<input placeholder="Discount code" />\n'
        rebuild(report)
    else:
        if mutation == "label":
            data = json.loads(end["summary"])
            data["elements"].append(dict(data["elements"][0], id="other"))
            end["summary"] = json.dumps(data)
            end["metadata"]["observation"] = save_artifact(
                Path(config.output_directory), "modified", end["summary"].encode(), ".json"
            )
        elif mutation == "flow":
            end["metadata"].pop("transition_record")
            end["metadata"].pop("transition")
        elif mutation == "action":
            end["metadata"]["transition_record"]["element_name"] = "invented"
            end["metadata"]["transition"] = save_artifact(
                Path(config.output_directory),
                "modified",
                json.dumps(end["metadata"]["transition_record"]).encode(),
                ".json",
            )
        rebuild(report)
    with pytest.raises(ValueError, match=message):
        validate_mappings(report, config, app, Inspector())


@pytest.mark.parametrize("mutation", ["revision", "owner", "source_hash"])
def test_wrong_code_graph_is_rejected(batch, mutation):
    report, config, app, code = batch
    mappings = validate_mappings(report, config, app, Inspector())
    node = code["nodes"][0]
    if mutation == "revision":
        node["revision"] = "c" * 40
    elif mutation == "owner":
        node["name"] = "Other"
    else:
        node["properties"]["sha256"] = "f" * 64
    with pytest.raises(ValueError):
        project_snapshot(code, mappings, config, app.project_id, REV)


def test_schema_documents_and_validates_config():
    schema = json.loads((ROOT / "schemas/ui-mapping.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    example = json.loads((ROOT / "configs/mapping/saleor.json").read_text())
    jsonschema.validate(example, schema)
    MappingConfig.model_validate(example)
    assert {k: v for k, v in schema.items() if k != "$schema"} == MappingConfig.model_json_schema()


def run_jsx(source, **changes):
    from trace_impact.ingestion.code import typescript_analyzer

    compiler = Path(typescript_analyzer.__file__).parent / "typescript/node_modules/typescript"
    payload = dict(
        path="src/view.tsx",
        source=source,
        label="Discount code",
        line=2,
        native_tag="input",
        observed_type="",
        wrappers=[],
    )
    payload.update(changes)
    return subprocess.run(
        [
            "node",
            str(ROOT / "src/trace_coordinator/tool/dependencies/validate_jsx.cjs"),
            str(compiler),
        ],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "source",
    [
        'export const Checkout = () => {\nconst text="Discount code"; return <input/>; };',
        'export const Checkout = () => (\n<input placeholder="Discount code" {...other}/>);',
        'export const Checkout = () => (\n<><input placeholder="Discount code"/><input placeholder="Discount code"/></>);',
        'export const Checkout = () => (\n<Button placeholder="Discount code" />);',
        'export const Checkout = () => (\n<input placeholder="Discount code" type="password"/>);',
        'export const Checkout = () => (\n<input placeholder="Discount code"/;',
    ],
)
def test_jsx_parser_rejects_text_outside_jsx_ambiguous_labels_spreads_and_unknown_wrappers(source):
    assert run_jsx(source).returncode != 0


def test_jsx_parser_accepts_native_and_conditional_label():
    result = run_jsx(SOURCE)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["owner"] == "Checkout"
    result = run_jsx(
        'export const Checkout = () => (\n<button type="submit">{busy ? "Applying..." : "Apply"}</button>);',
        label="Apply",
        native_tag="button",
        observed_type="submit",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "wrapper, succeeds",
    [
        (
            "export const Input = forwardRef(({className,...props}, ref) => <input ref={ref} {...props}/>);",
            True,
        ),
        ("export const Input = forwardRef(({placeholder,...props}, ref) => <input {...props}/>);", False),
        (
            'export const Input = forwardRef(({...props}, ref) => <input {...props} placeholder="different"/>);',
            False,
        ),
        ("export const Input = forwardRef(({...props}, ref) => <textarea {...props}/>);", False),
    ],
)
def test_jsx_wrapper_must_forward_identity_props(wrapper, succeeds):
    source = (
        'import {Input} from "./input";\nexport const Checkout = () => <Input placeholder="Discount code"/>;'
    )
    result = run_jsx(
        source,
        wrappers=[
            dict(module="./input", export="Input", path="input.tsx", native_tag="input", source=wrapper)
        ],
    )
    assert (result.returncode == 0) == succeeds, result.stderr


def test_inspector_checks_actual_patch_source_and_parser(batch, monkeypatch):
    from trace_coordinator.tool.dependencies import git_changes

    report, config, app, _ = batch
    monkeypatch.setattr(
        git_changes, "git", lambda _, *args: PATCH.encode() if args[0] == "diff" else SOURCE.encode()
    )
    inspector = knowledge.TypeScriptMappingInspector(app, config)
    candidate = {**report["ui_knowledge"]["code_ui_candidates"][0], "patch": PATCH}
    result = inspector.inspect(candidate, dict(tag="input", type=""))
    assert result["owner"] == "Checkout"
    for update, message in [({"patch": "bad"}, "Saved diff"), ({"line": 500}, "source excerpt")]:
        with pytest.raises(ValueError, match=message):
            inspector.inspect({**candidate, **update}, dict(tag="input", type=""))
    with pytest.raises(ValueError, match="Static JSX"):
        inspector.inspect(candidate, dict(tag="button", type=""))


def prepare_fixture(batch, monkeypatch):
    from trace_impact.ingestion.code.typescript_analyzer import TypeScriptAnalyzer
    from trace_impact.shared.graph_models import GraphSnapshot

    report, config, app, code = batch
    application_path = Path(config.application_file)
    application_path.with_name("graph-config.json").write_text(
        json.dumps(
            {
                "provider": "neo4j",
                "baseline_snapshot_file": app.graph_snapshot_file,
            }
        )
    )
    application_path.with_name("retrieval-config.json").write_text(
        json.dumps(
            {
                "provider": "qdrant",
                "ingestion_run_directory": app.ingestion_run_directory,
                "vector_directory": app.vector_directory,
            }
        )
    )
    application_path.with_name("ui-config.json").write_text('{"provider":"disabled"}')
    application_path.write_text(
        json.dumps(
            {
                "project_id": app.project_id,
                "repository": app.repository,
                "repository_path": app.repository_path,
                "change_source": app.change_source.model_dump(mode="json"),
                "graph_config_file": "graph-config.json",
                "retrieval_config_file": "retrieval-config.json",
                "ui_config_file": "ui-config.json",
                "baseline": {"url": app.baseline.url, "revision": app.baseline.revision},
                "patched": {"url": app.patched.url, "revision": app.patched.revision},
                "require_exact_revisions": False,
            }
        )
    )
    Path(config.report_file).write_text(json.dumps(report))
    Path(config.code_config_file).write_text(
        json.dumps(dict(project_id=app.project_id, repository_path=app.repository_path, revision=REV))
    )
    config = config.model_copy(
        update={"report_sha256": hashlib.sha256(Path(config.report_file).read_bytes()).hexdigest()}
    )
    path = Path(config.report_file).with_name("mapping.json")
    path.write_text(config.model_dump_json())
    monkeypatch.setattr(knowledge, "TypeScriptMappingInspector", lambda *_: Inspector())
    monkeypatch.setattr(TypeScriptAnalyzer, "analyze", lambda *_: GraphSnapshot.model_validate(code))
    return path


def test_preparation_is_local_repeatable_and_pins_report(batch, monkeypatch):
    path = prepare_fixture(batch, monkeypatch)
    first = knowledge.prepare_ui_snapshot(path)
    assert first[2] == knowledge.prepare_ui_snapshot(path)[2]
    assert verified_bytes(first[3]["snapshot"])
    Path(batch[1].report_file).write_text("{}")
    with pytest.raises(ValueError, match="review hash"):
        knowledge.prepare_ui_snapshot(path)


def test_preparation_rejects_mismatched_code_config(batch, monkeypatch):
    path = prepare_fixture(batch, monkeypatch)
    code = Path(batch[1].code_config_file)
    data = json.loads(code.read_text())
    data["project_id"] = "other"
    code.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Code configuration"):
        knowledge.prepare_ui_snapshot(path)


@pytest.mark.parametrize("failure", [None, "publish", "retrieval"])
def test_publication_receipt_only_after_verified_readback(batch, monkeypatch, failure):
    import trace_impact
    from trace_impact.shared.graph_models import ImpactResult

    path = prepare_fixture(batch, monkeypatch)

    class Pipeline:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def publish_code_graph(self, snapshot):
            if failure == "publish":
                raise TimeoutError("uncertain remote outcome")
            self.snapshot = snapshot
            return dict(graph_id=snapshot.id, nodes=len(snapshot.nodes), edges=len(snapshot.edges))

        def retrieve_impact(self, *args):
            return ImpactResult(
                status="UNMAPPED" if failure == "retrieval" else "OK",
                ui_ids=tuple(n.id for n in self.snapshot.nodes if n.kind == "UIElement"),
                flow_ids=tuple(n.id for n in self.snapshot.nodes if n.kind == "UserFlow"),
            )

    monkeypatch.setattr(trace_impact, "create_pipeline", lambda _: Pipeline())
    monkeypatch.setattr(trace_impact.Settings, "from_env", lambda: SimpleNamespace())
    if failure:
        with pytest.raises((ValueError, TimeoutError)):
            knowledge.publish_ui_snapshot(path)
        assert not (Path(batch[1].output_directory) / "committed").exists()
    else:
        receipt, saved = knowledge.publish_ui_snapshot(path)
        assert receipt["status"] == "PUBLISHED_AND_VERIFIED"
        assert json.loads(verified_bytes(saved)) == receipt


def test_map_ui_cli_preparation_and_publish(monkeypatch, capsys, tmp_path):
    from trace_coordinator.cli import main

    monkeypatch.setattr(
        knowledge, "prepare_ui_snapshot", lambda _: (None, None, None, {"status": "PREPARED"})
    )
    monkeypatch.setattr("sys.argv", ["trace-coordinator", "map-ui", str(tmp_path / "config.json")])
    main()
    assert "PREPARED" in capsys.readouterr().out
    monkeypatch.setattr(
        knowledge,
        "publish_ui_snapshot",
        lambda _: ({"status": "PUBLISHED_AND_VERIFIED", "graph_id": "new"}, {}),
    )
    monkeypatch.setattr(
        "sys.argv", ["trace-coordinator", "map-ui", str(tmp_path / "config.json"), "--publish"]
    )
    main()
    assert "PUBLISHED_AND_VERIFIED" in capsys.readouterr().out
