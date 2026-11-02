# Sprint 41b: separated finite-estimate minimum set, v1

Frozen before revised real selection on November 1, 2026. This is outcome-informed
method development motivated by Sprint 41, conditional on its curator labels.
Analysis B remains unrun; no semantic or reader-benefit validation is implied.

## Immutable population and scores

Use exactly `configs/curated-real-link-benchmark-41/curated-freeze-v1.json`, its
inventory, complete-page packets, curator scores, anchors and semantic tie IDs.
Retain all thirteen admitted pairs in all four development groups, including the
nonstationary Extensions singleton. Original inputs and numerical audits come
from `artifacts/embedded-link-refinement-34-36/s41-v1/selection-input` and
`selection-output`. `scripts/minimum_cost_set_41b.py baseline` records their
SHA256 inventory and the original receipt before revised selection. Historical
RA is `cf2eae9a4f1a805558446faa04dcfa0afc44974f`.

Fresh outputs: `artifacts/embedded-link-refinement-34-36/s41b-v1/`. No old output
is overwritten. Numerical policy remains v2: k=1, highest semantic tier, all
feasibility constraints, q=.25, seeds 3501/3502, eight starts, stationarity 1e-7,
identity tolerance 1e-9, rank epsilon 1e-6, exact block cancellation, frozen
original group weight, 600 seconds and 100 MiB per group, two CPUs/2 GiB and one
native thread. No new dependencies, model/outbound calls, labels or candidates.

## Explicit selector `retrieval_arena.embedded_selection.minimum_set.v1`

Old v2 calls and defaults are unchanged. New entry is restricted to k=1.
Failure precedence is resource exhaustion, then any unavailable highest-tier
candidate, then singleton. Failures never disappear from the tier. Invalid
finite costs (not exactly two finite schedules, or below -1e-9) are unavailable.
For comparison only clip permitted tiny negatives to zero; preserve raw scores.
Let L_i and U_i be the extrema of the two schedules. They are sensitivity
ranges, neither confidence intervals nor rigorous optimization-error bounds.

1. Preserve full-tier tie if max U - min L <= epsilon; select semantic-first S.
2. Preserve unique minimum if U_i + epsilon < L_j for every j != i.
3. Otherwise u=min U and C={i: L_i <= u+epsilon}. Accept only if |C|>=2,
   max_C U-min_C L <= epsilon, and every outsider L_j > max_C U+epsilon.
   Choose semantic-first within C using the frozen score/tie-ID/proposal order.
   If C is the full tier, label all-candidate tie and return S.
4. Otherwise return S with explicit unresolved status. No overlap chains or
   transitive clustering. All inequalities are literal, independent of input order.

The set comprises tied finite estimates, not certified global minimizers.
Trace member IDs/ranges/envelope spread/outsider gap/tie ID/fallback reason.
S and analytic q=0 Z remain unchanged and must agree. Score provenance binds
v2 numerical policy, graph, pool, candidate prefixes and stored solve keys;
selection provenance separately identifies this new rule. Recompute every saved
energy and full gradient and reconstruct comparisons from bound solve records
before cache reuse. Reject stale bindings rather than silently recompute.

## Qualification and real execution

Test the prompt's separated tie, semantic-inside, unique/full tie, empty/single,
overlaps/chains/wide envelopes, exact inclusive spread and strict gap boundaries,
failure precedence, permutations, tiny negatives, stale provenance and v2 parity.
Run a real tiny graph through scoring, minimum-set selection, editing and ledger
replay; then the official immutable focused suite.

Replay S/Z/M-v2 against stored results before producing M-set. Materialize isolated
original/S/Z/M-set copies with existing editor and externally anchored ledgers.
Independently verify every file, exact patch, graph delta, prose/navigation and
assignment. Freshly replay every changed group with identical numerical settings;
record numeric drift and winner stability. Seal primary outcomes before any
q=.10 diagnostic reuse (no new diagnostic solve is required).

Report complete group/candidate denominators, qualified costs, set spread/gap,
pair intersections and symmetric differences, replacements and fallbacks.
Inspect all selected source/destination cards. After sealing, export actual
changed blocks, directed arcs, weights and phases for every admitted candidate;
test directed weighted isomorphism with the added edge distinguished and provide
exact mappings. Derive simple-cycle explanations only where actual structure
qualifies; equal numbers or stationarity alone prove neither isomorphism nor
global optimality. Include representative diagrams and reproducible commands.

No manuscript, accepted claim, tags, release, reserved outcome or new study.
Related language guides are repeated development examples, not replications.
