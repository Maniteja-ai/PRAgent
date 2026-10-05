# Saleor retrieval dataset draft

This package is the first dataset deliverable for reviewing Neo4j and vector retrieval. It contains real frozen documentation passages, synthetic graph cases, input-only files, expected results, schema definitions and a human-review queue. It contains no evaluation runner and no measured precision or recall.

To set up or run the ingestion pipeline and its tests, start with the [ingestion README](../../../README.md). This dataset is not executable by itself: the ingestion project currently has no scorer for these labels. Agent-level report and code-graph evaluation commands are described in [`agents/evaluation`](../../../../agents/evaluation/).

**Status: assistant-authored, evidence-checked draft. Human review is pending. It is not approved golden data.** All cases belong to the development split because these sources and PR 1199 have already been inspected. No held-out accuracy claim is supported.

## Start here

1. Read the [review packet](review/review-packet.md), which places the questions beside expected facts and source passages.
2. Inspect the [coverage matrix](coverage-matrix.md). It distinguishes cases from unexecuted failure contracts and missing coverage.
3. Review exact values in [vector cases](labels/vector-cases.jsonl) and [graph cases](labels/graph-cases.jsonl).
4. Record approval or corrections in [the review queue](review/queue.jsonl), including reviewer and reason. Approvals must also update the case review metadata.
5. Freeze a new version only after review, schema validation and refreshed file hashes. Do not silently change a frozen dataset.

## Contents and scope

| Content | Count | Meaning |
| --- | --- | --- |
| Frozen documentation snapshots | 9 | Copies of saved normalized evidence, not fresh web fetches |
| Retrieval passages | 30 | Bounded, explicitly selected candidate universe |
| Vector questions | 40 | Includes 5 unanswerable questions and complete proposed 30-passage relevance labels per question |
| Graph fixture | 29 nodes, 32 edges | Synthetic dependency/UI/flow relationships; not observed Saleor mappings |
| Graph cases | 20 | Direction, multi-hop traversal, cycles, deduplication, uncertainty and scope |
| Isolation cases | 2 | Identical-text distractors from another project and a stale snapshot |
| Combined cases | 5 | Graph-plus-document evidence requirements, including incomplete evidence |
| Failure contracts | 8 | Expected handling of outages, bad vectors, partial results and source drift; not executed tests |
| Real PR evidence seeds | 4 observations | Pinned code and diff evidence only; real UI mapping labels still require review |

The 30 passages cover every saved source, but do not exhaust every paragraph or requirement. In particular, these relevance labels cannot establish recall across the existing 121-chunk production index. For that index, unjudged chunks remain UNJUDGED until separately annotated. Do not count them as irrelevant by default.

The bounded corpus makes the first benchmark auditable. Expanding it requires reviewing all query-to-new-passage labels and creating a new dataset version. No model outputs were used as ground truth: reference facts were drafted from source passages, but an assistant review is still not independent human approval.

## JSON files and definitions

`manifest.json` declares versions, hashes, counts, grading rules, file schemas and input boundaries. `schemas/dataset.schema.json` supplies strict typed contracts. JSONL files contain one complete JSON object per line; they are suitable for streaming and case-level review.

Target inputs contain no reference answers:

```text
inputs/vector/passages.jsonl       text, IDs and source provenance
inputs/vector/queries.jsonl        questions, scope and requested k
inputs/vector/isolation-decoys.jsonl
inputs/graph/fixture.json          nodes and edges
inputs/graph/queries.jsonl         changed symbols and query scope
```

`labels/` is scorer-only. `review/` contains the source snapshots, review packet, queue and pinned PR evidence. A future runner must enforce the manifest's allowlist rather than give the target access to the entire dataset directory. The manifest specifies that boundary; it does not enforce filesystem permissions by itself.

Source passages have exact Unicode character offsets, line ranges and SHA-256 hashes. Code files were read from pinned Git objects, so unrelated working-tree modifications did not affect the copied code. Original document URLs, document version caveats and upstream source ownership are retained. No secrets, API keys or live checkout IDs were included in target input files.

## Vector reference contract

The base corpus is exactly P01 through P30. Each case grades all 30 passages: 2 directly supports a required evidence unit, 1 supplies related context only, and 0 does not support the requested fact. Precision treats grade 2 as relevant. Evidence recall counts distinct required units; either of two acceptable passages can satisfy the same unit without double credit. Graded nDCG may use grades 0/1/2 on answerable cases only.

Questions with multiple required facts need every declared evidence unit. Empty evidence-unit lists mean unanswerable in this bounded corpus, not unanswerable everywhere. A raw nearest-neighbor search may still return passages for such a question; withholding an unsupported answer is an answer-selection responsibility. Do not fail raw Qdrant merely for returning its nearest neighbors.

Use an isolated evaluation index. Map dataset `snapshot_id` to the adapter's `run_id`, and freeze the chosen embedding profile when that index is built. The package contains text and labels, not precomputed model vectors. I01 and I02 add identical-text distractors to the test index and require project/snapshot filtering. Wrong embedding profile is separately specified as a failure contract.

Related question variants share group IDs. They must remain together if future versions introduce splits. All current cases remain development-only. P15 preserves a source inconsistency: the text says two boolean fields and lists three. The country-code question only uses the unambiguous mandatory-country sentence; do not turn the inconsistent count into a reference fact.

## Graph reference contract

These are synthetic topology tests for a future graph adapter. They do not use the current production requirement graph, and they do not certify impact semantics from mere reachability. The fixture IDs and revision labels are deliberately synthetic.

The operation is `impact_neighborhood`:

1. Require a nonempty project and revision. Resolve changed CodeSymbol IDs within that scope. A name-only lookup with multiple matches returns AMBIGUOUS_SYMBOL; it must not pick one arbitrarily.
2. Include the changed symbol itself. Follow incoming `DEPENDS_ON` edges to callers/dependents, for at most `max_dependency_hops` edges. Do not follow outgoing dependencies into callees. Track visited symbols so cycles terminate.
3. From those symbols follow confirmed `RENDERS` edges to UI elements. Find flows with confirmed `CONTAINS` edges pointing to those elements. Follow confirmed `CHECKS` edges from those flows to requirements.
4. Apply project and revision filters to every visited node, including intermediate nodes, and exclude unconfirmed or rejected edges. Cross-project/version bridge edges in the fixture are intentional distractors.
5. Return unique IDs. A flow reachable through two UI elements or paths is one result. Classify results as risk candidates; graph reachability does not establish a behavioral failure.

Hop limits apply only to dependency edges; UI/flow/requirement expansion follows afterward. Empty changed IDs without a lookup return NO_CHANGES. Unknown IDs or a nonexistent selected revision return SYMBOL_NOT_FOUND. An existing symbol without a reachable mapping is UNMAPPED, or UNMAPPED_WITHIN_BUDGET for the explicit limited-hop case. Both mean uncertainty, not proof of no real-world impact. Invalid scope is INVALID_INPUT.

Reference sets are exact for this contract and fixture. Witness paths demonstrate selected answers, not every possible valid path. A future scorer should verify path direction/type, scope and budgets, and permit alternative valid paths. Never require a particular Cypher string to obtain credit.

## Real Saleor work still needed

[PR evidence seeds](review/real-pr-seeds.json) pin the two changed checkout files and the upstream diff. They establish static code observations only. The earlier manually recorded browser checks are useful leads, not a complete autonomous flow inventory.

Before adding real impact gold, capture versioned UI states and transitions, adjudicate UI-to-code links, identify affected and unaffected controls, and record backend/fixture state. Document/API capability must not be promoted into a verified storefront feature. Before global vector recall, review relevant evidence across all 121 chunks. Before generalization claims, add fresh repositories, source groups and PRs that have not been used for tuning.

## Validation status

The manifest contains SHA-256 hashes, schemas, expected case counts and integrity rules. The original
standalone validator was part of the earlier package and has not yet been added to this compact
ingestion project. The ingestion package currently has no dataset validation or scoring command.
Preserve the draft status until a validator, human review and an evaluation runner are in place.
