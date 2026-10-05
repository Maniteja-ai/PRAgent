# PR agent quality evaluation

Run the evaluator from the `agents` directory:

```powershell
uv run python evaluation/agent_quality/run_quality_eval.py --run-id live-e2e-pr1-all-routes-20261005-0826
```

It reads the completed report and evidence from `data/agent-runs.sqlite3`, compares the report to `golden_dataset.json`, and makes one Gemini judge request. It does not run the agent, call Neo4j or Qdrant, or rerun ingestion. Results are written under `evaluation/agent_quality/results/<run-id>/` as `summary.md` and `evaluation.json`.

The summary includes:

- **Ground-truth precision/recall/F1:** whether report claims match expected PR impacts and whether expected impacts appear in the report.
- **Faithfulness:** whether atomic claims in findings are supported by the evidence IDs cited by those findings.
- **Relevance:** whether report claims and retrieved evidence apply to this PR.
- **Test-scope check:** whether the report describes code behavior as if the browser had exercised it.

The labels are currently `DRAFT_PENDING_HUMAN_REVIEW`. The LLM judge uses the configured model and is a diagnostic, not independent truth. Review the expected impacts and claim-level reasons before calling these scores a benchmark. The evaluation checks the final report; internal model prompts and generated Cypher are not stored in run history, so this evaluator does not score those internal calls.
