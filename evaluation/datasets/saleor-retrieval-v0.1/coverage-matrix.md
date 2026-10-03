# Retrieval dataset coverage

Cases specify expected behavior; no retrieval run has been executed. DRAFT_CASES does not mean human-reviewed golden data.

| Area | Scenario | Case IDs | Status |
| --- | --- | --- | --- |
| Vector | Direct factual retrieval | V01,V04,V12 | DRAFT_CASES |
| Vector | Paraphrases and typos | V02,V40 | DRAFT_CASES |
| Vector | Negation and false premises | V05,V11,V23,V39 | DRAFT_CASES |
| Vector | Multiple required evidence units | V31,V32,V33 | DRAFT_CASES |
| Vector | Related but wrong evidence | V03,V10,V24,V28 | DRAFT_CASES |
| Vector | Unanswerable bounded-corpus questions | V34,V35,V36,V37,V38 | DRAFT_CASES |
| Vector | Project and snapshot isolation | I01,I02 | DRAFT_CASES |
| Vector | Wrong embedding profile | F05 | FAULT_CONTRACT_ONLY |
| Graph | Direct and transitive reverse dependencies | G01,G02,G03 | DRAFT_SYNTHETIC_CASES |
| Graph | Cycles and duplicate paths | G04,G05 | DRAFT_SYNTHETIC_CASES |
| Graph | Missing or ambiguous mappings | G06,G07,G08,G09 | DRAFT_SYNTHETIC_CASES |
| Graph | Direction and hop budgets | G10,G11,G12 | DRAFT_SYNTHETIC_CASES |
| Graph | Cross-project and cross-revision links | G13,G14 | DRAFT_SYNTHETIC_CASES |
| Graph | Unconfirmed links excluded | G15 | DRAFT_SYNTHETIC_CASES |
| Graph | Unrelated flow, multiple changes and empty input | G16,G17,G18 | DRAFT_SYNTHETIC_CASES |
| Graph | Invalid scope or nonexistent revision | G19,G20 | DRAFT_SYNTHETIC_CASES |
| Combined | Complete evidence and missing upstream evidence | C01,C02,C03,C04,C05 | DRAFT_CONTRACTS |
| Operations | Timeouts, partial responses, quota and source drift | F01,F02,F03,F04,F06,F07,F08 | FAULT_CONTRACT_ONLY |
| Real graph | Autonomous UI-to-code mapping | None | NOT_YET_AVAILABLE |
| Corpus | All 121 chunks adjudicated for every query | None | NOT_YET_AVAILABLE |
| Generalization | Held-out documents, repositories and PRs | None | NOT_YET_AVAILABLE |
| Runtime | Latency, concurrent reads and large graphs | None | NOT_EXECUTED |
| Robustness | Prompt injection and multilingual queries | None | NOT_INCLUDED_IN_V0_1 |
