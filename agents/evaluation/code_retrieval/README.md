# Code impact retrieval evaluation

Run from the `agents` folder with `uv run python evaluation/code_retrieval/run_live.py`.

The dataset contains six PR-style code questions for the Saleor graph revision listed in the file. Expected files are direct consumers from the pinned ingestion graph (`IMPORTS`, `CALLS`, or `RENDERS` edges). This makes precision/recall reproducible, but it measures direct dependency recovery only; it does not claim that those files are the full behavioral impact set. The labels are **draft** and need independent human review before calling this a golden benchmark.

The runner queries the configured live Neo4j graph through the same `Neo4jCodeGraphRetriever` used by the agent, reports micro and per-case precision/recall/F1, then asks the configured Gemini model to judge the retrieved file snippets for relevance. The LLM judge sees every retrieved expected path and up to five extra paths per case, selected with a deterministic hash because graph results have no ranking. The LLM grades are a separate diagnostic and do not change the labeled precision/recall numbers. The output is written to `evaluation/code_retrieval/results/latest.json`.

To refresh only the LLM relevance grades from the saved graph results, without making another Neo4j query, run `uv run python evaluation/code_retrieval/run_live.py --judge-existing`.

An LLM judge can identify suspicious labels and irrelevant extra files, but it is not independent ground truth. Review the labels, add missed relevant consumers and negative cases, and version the approved dataset before treating its score as a release gate.
