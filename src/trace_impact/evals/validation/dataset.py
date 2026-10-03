"""Validate dataset structure and evidence integrity only; does not execute or score retrieval."""

import argparse
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator


def check(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        check(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def decode(text):
    def invalid_constant(value):
        raise ValueError(f"Non-JSON number: {value}")

    return json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid_constant)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate(root):
    root = Path(root).resolve(strict=True)

    def path(name):
        target = (root / name).resolve(strict=True)
        check(target.is_relative_to(root), f"Path escapes dataset: {name}")
        return target

    def read(name):
        return decode(path(name).read_text(encoding="utf-8"))

    def rows(name):
        return [decode(line) for line in path(name).read_text(encoding="utf-8").splitlines() if line.strip()]

    manifest = read("manifest.json")
    schema = read(manifest["schema_file"])
    Draft202012Validator.check_schema(schema)
    check(manifest["files"], "Missing file integrity inventory")
    actual_files = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    check(actual_files == set(manifest["files"]) | {"manifest.json"}, "File inventory does not match package")
    for name, expected in manifest["files"].items():
        check(sha(path(name).read_bytes()) == expected, f"File hash mismatch: {name}")
    records = {}
    for name, definition in manifest["file_schemas"].items():
        values = rows(name) if name.endswith(".jsonl") else [read(name)]
        validator = Draft202012Validator({"$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"})
        for value in values:
            validator.validate(value)
        records[name] = values
    json_files = {f for f in actual_files if f.endswith((".json", ".jsonl"))}
    check(json_files == set(records) | {manifest["schema_file"]}, "Unvalidated JSON file in package")

    def by_id(name):
        result = {v["id"]: v for v in records[name]}
        check(len(result) == len(records[name]), f"Duplicate IDs in {name}")
        return result

    passages = by_id("inputs/vector/passages.jsonl")
    decoys = by_id("inputs/vector/isolation-decoys.jsonl")
    check(not passages.keys() & decoys.keys(), "Decoy ID collision")
    inventory = records["review/source-inventory.jsonl"]
    source_ids = {x["source_id"] for x in inventory}
    check(len(source_ids) == len(inventory), "Duplicate source IDs")
    for source in inventory:
        check(sha(path(source["file"]).read_bytes()) == source["normalized_sha256"], "Source hash mismatch")
    for p in [*passages.values(), *decoys.values()]:
        evidence = p["evidence"]
        raw = path(evidence["source_path"]).read_bytes()
        text = raw.decode("utf-8")
        check(p["source_id"] in source_ids, "Unknown source")
        check(sha(raw) == evidence["source_sha256"], "Evidence source mismatch")
        check(0 <= evidence["start_char"] < evidence["end_char"] <= len(text), "Invalid evidence offsets")
        check(text[evidence["start_char"] : evidence["end_char"]] == p["text"], "Evidence span mismatch")
        expected_text = "".join(
            text.splitlines(keepends=True)[evidence["start_line"] - 1 : evidence["end_line"]]
        )
        check(expected_text == p["text"], "Evidence line range mismatch")
        check(sha(p["text"].encode()) == p["text_sha256"], "Passage hash mismatch")

    vectors = by_id("labels/vector-cases.jsonl")
    for case in vectors.values():
        gold = case["reference"]
        check(set(gold["candidate_universe"]) == set(passages), "Incorrect vector candidate universe")
        check(set(gold["relevance_grades"]) == set(passages), "Incomplete relevance matrix")
        units = gold["evidence_units"]
        check(len({u["id"] for u in units}) == len(units), "Duplicate evidence units")
        direct = {p for u in units for p in u["acceptable_passage_ids"]}
        check(
            direct == {p for p, grade in gold["relevance_grades"].items() if grade == 2},
            "Grade/evidence disagreement",
        )
        check(bool(units) == (gold["answerability"] == "ANSWERABLE"), "Answerability disagreement")
        for p in passages.values():
            check(
                all(p[k] == case["input"][k] for k in ("project_id", "snapshot_id")),
                "Unscoped vector universe",
            )

    fixture = read("inputs/graph/fixture.json")
    nodes = {n["id"]: n for n in fixture["nodes"]}
    edges = {e["id"]: e for e in fixture["edges"]}
    check(len(nodes) == len(fixture["nodes"]) and len(edges) == len(fixture["edges"]), "Duplicate graph IDs")
    types = {
        "DEPENDS_ON": ("CodeSymbol", "CodeSymbol"),
        "RENDERS": ("CodeSymbol", "UIElement"),
        "CONTAINS": ("UserFlow", "UIElement"),
        "CHECKS": ("UserFlow", "Requirement"),
    }
    for edge in edges.values():
        check(edge["source"] in nodes and edge["target"] in nodes, "Dangling graph edge")
        check(
            (nodes[edge["source"]]["kind"], nodes[edge["target"]]["kind"]) == types[edge["type"]],
            "Wrong edge endpoint types",
        )
    graphs = by_id("labels/graph-cases.jsonl")
    for case in graphs.values():
        gold, scope = case["reference"], case["input"]["scope"]
        check(case["input"]["fixture_id"] == fixture["id"], "Wrong graph fixture")
        selected = []
        for field, kind in [
            ("ui_ids", "UIElement"),
            ("flow_ids", "UserFlow"),
            ("requirement_ids", "Requirement"),
        ]:
            ids = gold[field]
            check(len(ids) == len(set(ids)), "Duplicate expected result")
            for id in ids:
                check(id in nodes and nodes[id]["kind"] == kind, "Expected result has wrong entity type")
                check(
                    all(nodes[id][k] == scope[k] for k in ("project_id", "revision")),
                    "Expected result escapes graph scope",
                )
            selected.extend(ids)
        check(not set(selected) & set(gold["forbidden_ids"]), "Expected and forbidden results overlap")
        for witness in gold["witness_paths"]:
            ids = witness["node_ids"]
            check(len(ids) == len(witness["edge_ids"]) + 1, "Invalid witness length")
            check(ids[0] in case["input"]["changed_symbol_ids"], "Witness has wrong origin")
            check(ids[-1] == witness["target_id"] and ids[-1] in selected, "Witness has wrong result")
            dependency_hops = 0
            phase = 0
            for i, eid in enumerate(witness["edge_ids"]):
                edge = edges[eid]
                check(edge["status"] == "CONFIRMED", "Witness uses unconfirmed edge")
                check({edge["source"], edge["target"]} == {ids[i], ids[i + 1]}, "Broken witness chain")
                if edge["type"] == "DEPENDS_ON":
                    check(phase == 0 and edge["target"] == ids[i], "Invalid dependency direction or phase")
                    dependency_hops += 1
                else:
                    next_phase = {"RENDERS": 1, "CONTAINS": 2, "CHECKS": 3}[edge["type"]]
                    check(next_phase == phase + 1, "Invalid mapping path order")
                    expected_origin = edge["target"] if edge["type"] == "CONTAINS" else edge["source"]
                    check(expected_origin == ids[i], "Invalid mapping direction")
                    phase = next_phase
            check(dependency_hops <= scope["max_dependency_hops"], "Witness exceeds dependency budget")
            check(
                all(all(nodes[id][k] == scope[k] for k in ("project_id", "revision")) for id in ids),
                "Witness escapes scope",
            )

    for name, cases in [("vector", vectors), ("graph", graphs)]:
        inputs = by_id(f"inputs/{name}/queries.jsonl")
        check(
            inputs == {id: {"id": id, **c["input"]} for id, c in cases.items()},
            "Target queries differ from labeled inputs",
        )
    combined = by_id("labels/combined-cases.jsonl")
    for case in combined.values():
        check(
            case["graph_case_id"] in graphs and case["vector_case_id"] in vectors,
            "Unknown combined case input",
        )
    isolation = by_id("labels/isolation-cases.jsonl")
    for case in isolation.values():
        check(case["base_case_id"] in vectors, "Unknown isolation base case")
        check(set(case["index_additions"]) <= set(decoys), "Unknown isolation decoy")
        check(
            set(case["forbidden_result_ids"]) <= set(case["index_additions"]), "Invalid isolation expectation"
        )
    failures = by_id("labels/failure-contracts.jsonl")
    all_cases = [
        *vectors.values(),
        *graphs.values(),
        *combined.values(),
        *isolation.values(),
        *failures.values(),
    ]
    ids = {c["id"] for c in all_cases}
    check(len(ids) == len(all_cases), "Case IDs collide across suites")
    queue = records["review/queue.jsonl"]
    check({r["item_id"] for r in queue} == ids and len(queue) == len(ids), "Review queue incomplete")
    if manifest["golden_release"]:
        check(manifest["status"] == "FROZEN", "Unfrozen golden release")
        check(
            all(c["review"]["approved"] and c["review"]["human_reviewers"] for c in all_cases),
            "Golden release lacks human approvals",
        )
        check(
            all(r["status"] == "APPROVED" and r["reviewer"] and r["reason"] for r in queue),
            "Golden release lacks adjudication",
        )
    for item in read("review/real-pr-seeds.json")["files"]:
        check(sha(path(item["file"]).read_bytes()) == item["sha256"], "Pinned code hash mismatch")
    counts = {
        "source_snapshots": len(inventory),
        "vector_passages": len(passages),
        "vector_cases": len(vectors),
        "graph_nodes": len(nodes),
        "graph_edges": len(edges),
        "graph_cases": len(graphs),
        "combined_cases": len(combined),
        "failure_contracts": len(failures),
        "isolation_cases": len(isolation),
        "isolation_decoys": len(decoys),
    }
    check(counts == manifest["counts"], "Manifest count mismatch")
    return {
        "structural_integrity": "PASS",
        "semantic_gold_approved": manifest["golden_release"],
        "retrieval_executed": False,
        "counts": counts,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.dataset), indent=2))
