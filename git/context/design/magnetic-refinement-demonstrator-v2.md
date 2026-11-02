# Magnetic-guided embedded-link demonstrator v2

Frozen November 1, 2026 after the finite Sprint 35b comparison. Recommended method:
**block-decomposed unit-phase marginal cost, used only within the best available
semantic-score tier, with deterministic abstention and semantic fallback.**
This is a repeatable approximate decision procedure. It does not estimate reader
benefit or certify global frustration minima. [Design review](../reports/2026-11-01-magnetic-refinement-35b-design-review.md),
[comparison freeze](magnetic-refinement-35b-comparison-plan.md),
[execution prompt](../prompts/36a-magnetic-refinement-demonstrator.md).

## Scope, units and boundary

Use Sprint 34's source bytes, substantive occurrence mask, original full parsed
graph and four **development** source-family groups, with zero halo. All their
pages remain in accounting, including isolates and the 830-page transitive group.
The four reserved groups remain unused. A source-family group prevents information
leakage; it is not presumed to be one connected graph or one optimization unit.
Every accepted addition stays within a baseline weak component of its induced
development group, with neither orientation already in the original parsed graph.
No external or reserved nodes enter a measurement support. Report omitted external
edges and mask ambiguities; no whole-corpus or nonzero-boundary claim follows.

The semantic unit is a proposed occurrence with source/target quotes and exact
UTF-8 byte spans. The graph unit is a unique new directed page pair. The numerical
support is the set of changed biconnected edge blocks after a tentative set of
additions. The comparison/reporting unit is a development group, not independent
links or independent corpus samples. Fixture scenarios have their own declared
budget and authored provenance.

## Quantity and exact reduction

For binary directed A, W=(A+Aᵀ)/2 and θ=2πq(A−Aᵀ), use the existing RA energy

E_G(φ)=Σ_{i<j} 2Wᵢⱼ[1−cos(φᵢ−φⱼ−θᵢⱼ)].

The primary q is .25; .10 is diagnostic and never changes selection. Let
F(G)=min_φ E_G(φ). For a set P of new nonreciprocal pairs, the desired quantity is
Δ(P)=[F(G+P)−F(G)]/B, B=Σ_{old i<j}Wᵢⱼ>0. B is frozen for the entire source group
and every prefix. Added total energy cannot decrease at the true optimum because
each new term is nonnegative. Lower cost means less additional directional
inconsistency at a common edit budget, conditional on independent editorial
support. It does not mean existing inconsistency was repaired.

First, F is additive across weak components, since their phase variables are
independent. Unchanged components cancel in the marginal quantity. Reuse their
identical cached contribution if a total is requested; never independently solve
and subtract an unrelated component twice.

The same exact minimum decomposes further across maximal biconnected **edge**
blocks of the undirected support, counting bridges as two-node blocks. Every
unordered pair belongs to exactly one block. Each block's minimum exists on its
compact phase torus. Starting from any root block, rotate each child block's
entire minimizing assignment to match its parent's shared articulation phase.
The block-cut incidence graph is a forest, so there is no second shared boundary
constraint to contradict that rotation. This constructs a global assignment
attaining the sum of block minima. Conversely, every assignment is at least the
sum of independent block minima. Hence F(G)=Σ_b F(b), with isolates contributing
zero. This derivation is specific to phase-difference energy with free gauges.

Partition G and G+P; cancel identical blocks by canonical node/arc hash. Additions
can merge several old blocks into one new block. Solve that new block jointly and
subtract the old participating blocks. Multiple edits cannot be scored by summing
their isolated marginal costs. Never cut a large biconnected block into selected
cycles or endpoint neighborhoods. The support is exact and basis-independent;
there is no chosen cycle basis or artificial Dirichlet boundary. If a new block
is too costly, return unavailable under the numerical ceiling.

The paper's symmetric normalized magnetic matrix uses the same W and θ, but its
minimum allows varying amplitudes. For zᵢ=exp(iφᵢ) and y=D^(1/2)z, its Rayleigh
quotient is E_G(φ)/(2B). Thus the unit-phase problem is related to the normalized
relaxation; this selector uses a frozen-denominator energy difference, not the
change in normalized lambda1. Adding edges changes degrees. Block additivity
does not apply to the normalized minimum. Retain complete-cluster diagnostics
only as descriptive final outputs, not selection inputs.

## Finite numerical contract

RA `graph/block_phase.py` and the [contract](../../RetrievalArena/docs/evaluations/block_phase.md)
implement the bounded prototype. Each changed block is solved with the unchanged
L-BFGS-B implementation: eight deterministic starts, maxiter 2000, ftol 1e-15,
gtol 1e-8, maxls 40, and required full gradient infinity norm ≤1e-7. Run schedules
with seeds 3501 and 3502 independently. Keep every initial/final phase, energy,
iteration count, optimizer termination and independently recomputed gradient/energy.
Choose the lowest-energy stationary result in each schedule, not the most
favorable schedule. No eigenvector phase extraction, adaptive restart, warm start
from a favored candidate, or tolerance change is allowed.

Denote the resulting sum of block estimates U_s(G). It is a feasible upper bound
for F(G) after gauge stitching; U_s(G+P)−U_s(G) is **not** a bound on the true
marginal optimum. Report it as a finite-algorithm estimate. Recompute cached
energies with 1e-9 identity tolerance; compare in raw energy units with 1e-6 rank
floor before normalizing. Cache keys bind canonical nodes/arcs, q, algorithm
version, full policy and seed; persist records so all arms receive identical
numerical effort on identical graph states. Do not include canceled-block solver
noise. Nonstationary/worse starts remain visible but do not veto another valid
stationary solution. Stationarity does not certify a local or global minimum.

For each candidate prefix save both schedule costs and their min/max. These
ranges measure sensitivity to two finite schedules, not confidence intervals or
optimization-error bounds. Statuses are `finite_estimate`, `nonstationary`,
`identity_failure`, `negative_marginal`, `resource_limit` and `invalid_edit`.
A marginal estimate below -1e-9 is a numerical failure. A tiny negative inside
tolerance is reported unchanged, treated as zero only for tie handling. There is
no replacement by q=.10, a fixed-tree score or a spectral descriptor.

Forests, isolates, balanced components and cycle aliases stay named topology
statuses. A forest can acquire frustration when an addition closes a cycle.
At q=0, a constant phase gives exactly zero energy for any graph; the disabled
control uses this analytic value. A zero magnetic score at q=.25 can reflect
holonomy aliasing, not a correct reading sequence. Global reversal conjugates
phases and preserves optimal energy; the semantic rubric handles link direction.

## Shared candidate pool and semantic support

The authored [six-page fixture](../configs/magnetic-refinement-35b/workflow-fixture-v1.json)
has a declared path of content links plus retained previous/next navigation.
Its two scenarios use budget one: a discriminating overview→validation versus
overview→checklist choice, and translated equal-cost shortcuts. Equal semantic
scores of six are **designed fixture labels**, not human or model judgments.
The first tie order deliberately places validation first. State this construction
when showing S/Z versus M. The mechanism witness does not establish that skipping
to a checklist is preferable. Do not present the primary charge's chosen link as
semantically superior; the diagnostic charge reverses this constructed choice.

For real Docker, broaden the old access bottleneck by design, once, before any
new generation. In each fixed development group:

1. Identify all host-admissible plain paragraphs using RA's existing parser/edit
   restrictions. Sort source pages by SHA256([3602,group_id,source_id]); keep the
   first ten with at least one permissible target. No quota transfers.
2. Expose at most three paragraphs per source, chosen by SHA256([3602,source_id,
   raw_start,raw_end]) rather than first-paragraph order. Each is at most 3000
   Unicode characters; omit overlong paragraphs, no mid-sentence truncation.
3. Rank permissible targets within the same baseline component by the maximum
   token-set Jaccard overlap between a disclosed source paragraph and any target
   paragraph of frozen semantic text. Use v1 lowercase Unicode tokenization,
   no embeddings or fitted stopword lists; ties by target ID. Keep five targets.
   For each expose its title and the two highest-overlap complete paragraphs,
   at most 2000 characters each, with raw-byte maps. The host records every
   omitted/overlong paragraph and unavailable mapping. No undeclared file access.
4. One generation packet per source permits up to two alternatives among these
   five targets and three source paragraphs: at most 20 proposed occurrences per
   group, 80 overall. Generator sees no magnetic information or arm. Freeze the
   access inventory and all source bytes before calls; no regeneration for ties,
   low coverage or disappointing scores.

Retain v1's seven relation types and exact source/target evidence. `source_requires_target`
navigates source→target while prerequisite order is target-before-source. Type is
recorded for audit, not converted to a phase sign/weight. The separate reviewer
scores relationship support, target evidence, and anchor placement 0–2 each;
each ≥1, sum ≥5, no contradiction and uncertainty=false are required. One scalar
semantic score 5 or 6 defines the tier. Quotes must verify against raw bytes.
Substantive rejection is final; only manifest-bounded technical failures may retry.
Model judgments are AI-reference evidence; a typed rationale alone validates nothing.

## Matched selection, conflicts and abstention

Use the same technically and semantically eligible pool for S, Z and M. Reject
both proposed reciprocal partners; preserve duplicate-pair, overlapping-anchor,
same-sentence and same-source conflicts. Maximum one selected edit per source
file avoids rebasing old coordinates. Compute B_edit=min(4, maximum conflict-free
set size) by exact enumeration of up to 20 candidates, **before magnetic scoring**.
All choices must preserve completion to this common budget. Zero-budget groups
stay present. The fixture overrides only the declared maximum to one.

S takes highest semantic score then canonical ID. Z uses the same algorithm with
the magnetic term disabled (q=0 analytic zero) and must equal S. M's choices at
each step are all completion-preserving candidates in the **highest current
semantic tier**; there is no top-three shortlist. This permits magnetic decisions
among equally rated options without forcing a lower-rated link.

For every choice c, score the entire tentative prefix P∪{c} relative to the original
G using exact block cancellation. Ranking cumulative costs is equivalent to ranking
the next marginal cost, because P is shared. With [l_c,h_c] the two-schedule raw
range, choose c only if h_c+1e-6 < min_{d≠c} l_d. If all candidate costs lie within
one 1e-6 band, use S's tie order. If there is only one choice, use it with no
guidance claim. Any unavailable candidate, overlapping unresolved ranges, or
partial minimum tie with other higher alternatives triggers **semantic fallback
for that step over the entire tier**, not silent deletion of difficult candidates.
This deliberately conservative rule matches the prototype `ordering` contract.
Record fallback reason; fallback never counts as a magnetic-guided edit.

This guarantees common feasible budgets without hiding numerical coverage loss.
Two outputs must be distinguished: per-step availability/resolved ordering and
the final paired edited corpora. A resolved magnetic winner identical to S is a
resolved ranking, but not a changed decision. If M changes a choice at one step
and later falls back, label the arm mixed. No population p-values or link-level
independence claims. Final energy computation uses identical schedules for S/M;
all rejected candidate solves and runtime remain part of M's cost.

## Outputs, ceilings and migration

Sprint 36a must implement the full selector/ledger/patch integration; the current
prototype performs numerical comparisons only. It must produce original/S/Z/M
copies, unified diffs, exact inserted anchors, graph deltas, before/after visible
text and content/navigation hashes, and an HTML case-card index. Record every
proposal through distinct proposed, schema/structural validation, review,
eligibility, selection, application and verification events, with original response
hashes and trusted ledger tips. Mark fixture provenance `authored_fixture`; never
forge a provider response or describe it as inference. Real proposals use the
actual provider identity and cost record. Inspect rendered fixture cards and at
least every changed real source page; text/graph equality checks cover all files.

Report all four groups: legal anchors, permissible new pairs, disclosed source/
target counts, proposed/structural/semantic eligible counts, conflict-free budget,
semantic-tier sizes, scorable/resolved/tied/unavailable choices, guidance-caused
selection changes, fallback counts, requested/achieved budgets and costs. Causes
such as no anchors, no permissible pairs, no supported targets, one candidate,
all ties and unresolved numerics are separate. Source mask semantics and practical
opportunity remain unvalidated. Freeze deterministic access before diagnosing
opportunity; never resample for arm divergence.

Offline numerical ceilings: two CPUs, 2 GiB, one BLAS thread; at most 600 seconds
per group, 2400 seconds total real selection, 100 MB numerical output per group.
Use a process watchdog in 36a so a single solve cannot overrun the wall ceiling.
Finish the matched copies with explicit semantic fallback if a group's numerical
budget expires; retain partial attempted records. Candidate access construction
has a separate 600-second ceiling and no network. Maximum four edits per arm per
group, at most 48 applied real occurrences across S/Z/M; duplicates across arms
remain separately applied copies but deduplicated for independent review.

For a later authorized AI-reference demonstration, recommend Luna generation,
Haiku eligibility and Sol blinded final assessment. The concrete provider IDs,
prompt/schema versions, source disclosure, tokens, attempts, pricing and image
must be verified and frozen in 36a before asking for the author's explicit yes.
Proposed hard limits: 80 generation attempts (40 initial+one technical retry),
160 eligibility attempts (80+retry), 96 final-assessment attempts (48+retry),
including probes; concurrency two, 120 seconds/call, two-hour total, USD 20 hard
cap with per-call worst-case reservation. Deduplicate identical selected links
before final assessment. If current tariffs cannot fit these caps, reduce the
manifest before approval; never silently exceed or substitute a model. No model
approval is implied by this design.

The 34 parser/masks, exact edit API, grouped assignments and typed ledger are
reused. The 35 graph convention, energy kernel and finite solver are reused
unchanged. V2 replaces whole-group independent solving, all-start vetoes,
restart-spread-as-rank-gate, first-paragraph/top-three access and blanket arm stop.
V1's failed result and conditional 36 specification remain historical and unrun.
No new norm, charge search, neighborhood truncation, manuscript change or benefit
claim is introduced. The [later utility outline](magnetic-refinement-utility-study-outline.md)
is separate from this mechanism demonstration.
