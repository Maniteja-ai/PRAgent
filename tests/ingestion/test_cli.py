"""Public CLI ingestion entry points, exit codes, and secret-safe diagnostics."""

import json
import logging
import sys
from pathlib import Path

import pytest

from trace_impact.cli import main

ROOT = Path(__file__).resolve().parents[2]


def invoke(monkeypatch, tmp_path, *args):
    monkeypatch.setattr(sys, "argv", ["trace-impact", "--env-file", str(tmp_path / "absent.env"), *args])
    return main()


def test_cli_collect_and_validate(tmp_path, monkeypatch, capsys):
    config = str(ROOT / "configs/ingestion/example/project.json")
    invoke(monkeypatch, tmp_path, "validate-project", config)
    assert json.loads(capsys.readouterr().out)["project_id"] == "example-library"
    invoke(monkeypatch, tmp_path, "collect", config, "--output", str(tmp_path / "runs"))
    result = json.loads(capsys.readouterr().out)
    assert result["documents"] == 1 and result["chunks"] > 0 and result["errors"] == []
    assert (Path(result["run_dir"]) / "corpus.json").is_file()


def test_cli_doctor_never_prints_keys(tmp_path, monkeypatch, capsys):
    logger = logging.getLogger("trace_impact.events")
    previous = (list(logger.handlers), logger.level, logger.propagate)
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-secret-must-not-leak")
    invoke(monkeypatch, tmp_path, "doctor")
    output = capsys.readouterr()
    assert json.loads(output.out)["GEMINI_API_KEY"] is True
    assert "fixture-secret-must-not-leak" not in output.out + output.err
    assert (logger.handlers, logger.level, logger.propagate) == previous


@pytest.mark.parametrize(
    "args",
    [
        ["validate-project", "missing-project.json"],
        ["extract", "missing-run", "--max-chunks", "0"],
    ],
)
def test_cli_invalid_input_exits_nonzero(tmp_path, monkeypatch, capsys, args):
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, tmp_path, *args)
    assert error.value.code == 1
    assert "Invalid configuration/input" in capsys.readouterr().err


def test_cli_exports_editor_schema(tmp_path, monkeypatch, capsys):
    target = tmp_path / "schema.json"
    invoke(monkeypatch, tmp_path, "schema", "--output", str(target))
    schema = json.loads(target.read_text(encoding="utf-8"))
    assert schema["title"] == "Trace Impact ingestion project"
    assert "Project schema written" in capsys.readouterr().out


def test_cli_exports_project_metadata_definitions(tmp_path, monkeypatch, capsys):
    data = json.loads((ROOT / "configs/ingestion/example/project.json").read_text(encoding="utf-8"))
    data["metadata"] = {
        "fields": [{"name": "area", "title": "Area", "choices": ["catalog", "checkout"]}],
        "defaults": {"area": "catalog"},
    }
    config = tmp_path / "project.json"
    config.write_text(json.dumps(data), encoding="utf-8")
    target = tmp_path / "schema.json"
    invoke(monkeypatch, tmp_path, "schema", "--config", str(config), "--output", str(target))
    schema = json.loads(target.read_text(encoding="utf-8"))
    values = schema["$defs"]["Source"]["properties"]["metadata"]["properties"]
    assert values["area"]["enum"] == ["catalog", "checkout"]
    assert "Project schema written" in capsys.readouterr().out
