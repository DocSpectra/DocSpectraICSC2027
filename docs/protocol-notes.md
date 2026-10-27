# Protocol notes: the Docker whole-corpus refinement run

These notes describe the Docker refinement experiment as it was run, including every change to the protocol made after the run began. The protocol, its admission rules and its analysis were frozen before the first editing session (evidence `a04c70c5`, kept in this repository). Departures are listed with their reason and effect. The documentation versions, questions and judgments are in [DocSpectra/DockerDocsSet2026](https://github.com/DocSpectra/DockerDocsSet2026), whose `docs/refinement.md` describes the editing run and whose `protocol/` directory holds the per-session records.

## Author amendment made after outcomes were seen

**AM1, incoming-link exemption for templated pages.** The admission rule C1b requires every new page to have an incoming Markdown link from another page. After all 25 area passes in both arms, and before reconciliation, the author exempted two page types from C1b in both arms: a new page under `content/includes/` that some page references with an `include` shortcode, and a new section `_index.md` whose folder holds another page. The reason: the link graph excludes Hugo templating by design, so a page reached only through a shortcode or navigation can never have a Markdown in-link. Reachability was checked before deciding (the include was used on at least two pages; the index linked its four child pages). The decision was made after outcomes were seen, and only LT had failed C1b. LT areas A10 and A12 were rerun as new editing sessions (LT-r28, LT-r29) from the current LT tree. Both reruns passed C1b without the exemption, so AM1 decided no admitted session; its effect is that two LT areas received a second editing session. The exemption is implemented as `C1B_EXEMPTION` in `git/context/scripts/wc_refine_17.py` and, with fix F3, as RetrievalArena's `structural_connectivity_constraint.v2` (commit `60af29d`).

## Implementation fixes during the run

Each fix corrects how a frozen rule was computed, not the rule's intent. Each was applied to both arms from the time it was found. A session decided under a defect was re-evaluated when its arm had run nothing since, and otherwise rerun.

| ID | Defect | Outcomes changed |
| --- | --- | --- |
| F1 | Docker network address-pool exhaustion was recorded as an editing failure | sessions that never reached the editor are voided and rerun; LT-r01 voided |
| F2 | The admission resolver ignored Docker's documentation-site URLs (`/dhi/…` for `content/manuals/dhi/…`), so valid links counted as broken | R0 A21 re-evaluated and admitted |
| F3 | C2 measured the algebraic connectivity on a set that omitted formerly isolated pages the edit had linked in, and scored 0 when that set was disconnected | every LT area admission passes C2 only under F3 (all LT values are 0 without it); LT-r03 (A21) passes C2 under F3 |
| F4 | The editor's login refresh was discarded in the session's temporary storage, so the login expired | R0-r03 voided; the session login was restored |
| F5 | Protected-line preservation compared lines exactly, so a command kept with or without its `$ ` prompt counted as lost | LT A24 re-evaluated and admitted |
| F5b | Collapsed internal whitespace counted as a lost protected line | LT A18 re-evaluated and admitted |
| F5c | A code-fence opener with an unclosed attribute brace counted as lost when the edit closed the brace | LT reconciliation re-evaluated and admitted |
| F6 | The frozen helper for the structural test's per-area view took every edit endpoint of the variant; on a whole-corpus tree that is the whole arm, so every area got the same subgraph and the test would have returned its minimum p automatically | the per-area view uses only that area's added and removed edges against matching per-area null components; the primary test is reported only under F6 |
| R1 | LT A21's first attempt (LT-r03) was rejected only because of F2, F3 and F5 | the area was reopened and rerun once (LT-r15, admitted); the first attempt's cost still counts |

A zero-call replay of `structural_connectivity_constraint.v2` equals the driver's final rule on all 56 refinement trees with an admission receipt and reproduces all 53 effective admission decisions ([`wc-c-v2-replay-v1-2026-10-19.json`](../git/context/evidence/wc-c-v2-replay-v1-2026-10-19.json)).

## Other departures

| ID | Departure | Reason |
| --- | --- | --- |
| D-P1 | Before any session, the run plan was revised: a stale sentence about a 60-minute, 150-path limit was removed from both base prompts, and R0's prompt used neutral "reorganization goal" wording instead of "target structure" | the old sentence contradicted the run's limits, and R0 has no target structure |
| D-P2 | The frozen protocol document gained an author addendum (48-hour checkpoint and projection rules), accepted by an exact-prefix check | rules added on October 18, before the run |
| D-P3 | The run paused before reconciliation for an author review of the C2 and C1b rules, not at the planned 48-hour checkpoint (all passes took about 11.5 hours) | the C2 and C1b effects on LT surfaced mid-run |
| D-P4 | The blinded factual review of edited pages (40 pages, 20 per arm) took place after the structural results were known | author scheduling; the verdict criteria and stop rule were frozen and the packet hashed before review; LT's new trunk pages may be recognizable |
| D-P5 | After answer scoring and before any arm contrast, the 1,192 tier-2 question–page pairs left `undetermined` by 12-line windows were judged again on the whole page by the same verifier and prompt (input cap raised from 16,000 to 400,000 bytes), and the common question set was rebuilt on the new decisions: 105 questions instead of 101 | an author check found an answer spread over more lines than one window holds |

## Sensitivity analyses

Each is computed with the same code, nulls and seeds as the main result and reported beside it, never instead of it.

| ID | Variant | Result or statement |
| --- | --- | --- |
| SA1 | Drop LT A10, A12 and A21 (the areas given a second session) | n = 15, mean −1.07, p = 0.044, beside the main n = 18, mean −0.80, p = 0.079 |
| SA2 | Frozen C2 without F3 | LT has no admitted area, so there is no LT realization to test |
| SA3 | Fix dependence | the fixes each admitted area needed, per arm |
| SA4 | Paired areas only | identical to the main test, which already uses only areas edited by both arms |
| SA5 | `undetermined` tier-2 pages scored as hits instead of misses | an effect is called robust only if its sign holds both ways |
| SA6 | Tier-2 question scope bracket and decomposition of derived-page (O1) credit | strict and lenient bounds; O1 recomputed with surviving-page credit only |
| SA7 | Without the six questions an author check found unanswerable in both versions | recomputed tier-2 answer-quality contrasts |

SA5, SA6 and SA7 were added by the author after answer scoring, before any arm contrast was computed and blind to arm. The structural test is two-sided at 0.05; its smallest attainable p is reported with each result. The recorded values are in [`wc-spectra-18-v1-2026-10-19.json`](../git/context/evidence/wc-spectra-18-v1-2026-10-19.json) and [`wc-analysis-20-v1-2026-10-24.json`](../git/context/evidence/wc-analysis-20-v1-2026-10-24.json).

## Final counts

LT has 18 of 25 areas admitted and R0 has 23 of 25; both reconciliations were admitted. Before AM1 and the reruns, the counts were LT 16 and R0 23.
