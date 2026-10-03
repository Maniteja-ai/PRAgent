# Loom walkthrough script (7 minutes)

The recording itself requires your Loom account and voice. This script keeps the demonstration inside the requested 5–10 minute window.

## 0:00–0:45 — Problem and scope

Open `README.md`.

Say: “This prototype answers one question: when checkout code changes, which UI controls, user flows, and product requirements are at risk? I chose Saleor and went deep on voucher application and removal. The system connects documentation, Neo4j code/UI/requirement relationships, vector retrieval, and a live browser comparison.”

## 0:45–1:35 — Architecture

Open `deliverables/DESIGN_DOCUMENT.md`, section 2.

Explain the two packages: the reusable ingestion/retrieval/eval library and the LangGraph coordinator. Point out that deterministic tools resolve diffs, query stores, enforce budgets, and verify behavior; the LLM produces a structured impact decision from supplied evidence.

## 1:35–2:25 — Configuration and safety

Open:

- `configs/ingestion/saleor/project.json`
- `packages/coordinator/configs/saleor-verified.json`
- `packages/coordinator/configs/verification/saleor-voucher.json`

Show that provider choices, models, paths, limits, and scenarios are JSON validated. Mention the five-call hard cap per agent/tool, allowed browser controls, fresh guest checkouts, and the rule forbidding order/payment submission.

## 2:25–3:20 — Ingestion, retrieval, and graph

Open `docs/current-ingestion-run.md` and `docs/graph-schema.md`.

Say: “The saved Saleor run contains nine documents and 121 indexed chunks. Qdrant stores semantic evidence. Neo4j stores explicit code, UI, flow, and requirement paths. Absence is a CoverageAssessment such as not observed, blocked, not explored, or ambiguous—it is never inferred from a missing edge.”

## 3:20–4:50 — Live product scenario

Open both deployments:

- <https://testsigma-saleor-baseline.vercel.app/default-channel/products>
- <https://testsigma-saleor-patched.vercel.app/default-channel/products>

Then show the command:

```powershell
cd packages/coordinator
uv run --no-sync trace-coordinator run configs/saleor-verified.json examples/saleor-request.json --run-id loom-demo --output artifacts/loom-demo
```

Use the already completed `submission-pr-1199-02` report if a new live run would take too long. Explain that both deployments attest their source revisions and share a backend. The baseline displays voucher controls but does not apply the eligible code; the patched build applies the 10% voucher and removes it successfully.

## 4:50–5:50 — Report for QA

Open `deliverables/SAMPLE_OUTPUT_PR_1199.md`.

Walk through the decision, UI areas at risk, affected flow, observed baseline/patched table, requirements, recommended checks, and limits. Emphasize the distinction between potential structural impact and behavior actually observed.

## 5:50–6:35 — Evals

Open `packages/coordinator/artifacts/final-evaluation-01/report.md`.

Show the 17 machine checks, 40-query retrieval benchmark, Neo4j contract evaluation, 100-run coordinator stability, live LLM grounding checks, and end-to-end gate. State clearly that the six-real-PR dataset is still pending independent label review and that its development score is not a production accuracy claim.

## 6:35–7:00 — Scope and next steps

Return to the design document’s scope section.

Say: “I cut universal crawling, payments, automatic mapping confirmation, and a hosted worker. With another week I would first obtain independent PR labels, then add runtime code-to-component attribution, then deploy the webhook worker with durable storage.”

End with the GitHub repository URL and the two live storefront URLs.
