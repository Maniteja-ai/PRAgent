# Demo runbook (5–8 minutes)

This is a recording guide for the assessment walkthrough. The live demo run and report are already saved; record your own screen and upload it to Loom, then add the Loom URL to the submission. Do not show terminal environment variables, `.env` files, tokens, PEM keys or credentials.

## Before recording

- Open the public repository README and the assessment documents.
- Open the sample report and the saved live report in adjacent tabs.
- Have PR [Maniteja-ai/storefront#1](https://github.com/Maniteja-ai/storefront/pull/1) ready.
- Keep credentials and local secret files off-screen.

## Walkthrough

**0:00–0:45 — Problem and goal**  
“Given a pull request, QA needs to know which customer journeys may change and what evidence supports that. This prototype gathers code/document/UI knowledge separately, then produces a traceable PR impact report.”

**0:45–1:45 — Repository and separation**  
Show the repo root. Point out `ingestion/` and `agents/`. Explain that ingestion prepares code/document chunks for Qdrant and typed code/route relationships for Neo4j; the webhook-driven agent reads those stores when a PR arrives.

**1:45–3:00 — Ingestion and graph/vector roles**  
Show `ingestion/configs/saleor.json` and its six referenced files, without opening secrets. Explain that vectors retrieve relevant text while Neo4j traverses explicit code and route relationships. State that the current mapping is incomplete: four route mappings do not equal a complete control-level or requirement-to-UI graph.

**3:00–4:00 — PR workflow**  
Show PR #1 and identify the two changed checkout files. Describe the pipeline: validate webhook request, load PR/diff, retrieve evidence, run configured browser checks, generate findings, apply guardrails, save report and history. Mention that diff evidence is hashed and findings cite evidence.

**4:00–5:30 — Report and browser limits**  
Show the QA sample report. Walk through apply/remove/refresh impact predictions and the explicit browser check list. Emphasize that the browser only saw checkout URL, discount input and Apply button; it did not submit/remove codes or validate totals. Explain why `COMPLETED_WITH_GAPS` is the honest status.

**5:30–6:30 — Evaluation**  
Show the saved evaluation summary. Explain precision/recall/faithfulness and point out evidence relevance is 56.2%. Say clearly that all scores come from one draft case and one LLM judge and require human-reviewed labels before they can serve as a benchmark.

**6:30–7:30 — Tests and next priorities**  
Show test command/output or README: 87 agent tests and 28 ingestion tests pass locally. Close with the three next priorities: autonomous UI/control mapping, real voucher behavior tests, and a broader human-reviewed evaluation dataset.

## Submission checklist

- GitHub repository URL: `https://github.com/Maniteja-ai/testsigma-impact-agent`
- Design document: `docs/assessment/design-document.md`
- QA sample report: `docs/assessment/sample-pr-impact-report.md`
- Loom recording URL: add after you record and upload the walkthrough.
