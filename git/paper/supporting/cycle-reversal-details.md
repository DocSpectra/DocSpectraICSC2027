# Cycle-reversal diagnostics retained outside the main paper

## Current main-paper evidence: Sprint 23b, adopted October 31

The main summary now covers all fourteen frozen corpora: 77 selected reversals in twelve eligible corpora, 54 resolved in ten at q=.25. Its [generator](../scripts/prepare_cycle_summary.py) transcribes the frozen per-corpus evidence into [Table I](../tables/w02-matched-summary.tex); [current provenance](../cycle-summary-provenance.json) records every displayed value. Empty frames and unresolved pairs remain visible. The median and maximum include unresolved numerical deltas; they are not population estimates or practical-effect thresholds.

See the [23b report](../../context/reports/2026-10-31-magnetic-attribution-23b-results.md), [run evidence](../../context/evidence/magnetic-attribution-23b-run-v1-2026-10-31.json), [selection](../../context/evidence/magnetic-attribution-23b-selection-v1-2026-10-31.json), and [plan](../../context/evidence/magnetic-attribution-23b-plan-v1-2026-10-31.json). Full distributions, selected arcs, case cards and localization plot remain in the local study artifacts. No new public release is implied. Sprint 31c's AI-purpose study remains artifact evidence and is not added to the main paper.

## Historical three-anchor study (Sprint 23)

The former main-paper summary used `tables/w02-matched-summary.tex`: resolved counts, largest absolute percentage change among all four selected cycles, and baseline effective support. These are descriptive summaries of topology-selected interventions, not estimates of a typical response. A dash in the maximum column means no change resolved; it does not mean exact zero.

The complete twelve-pair table remains in [w02-matched.tex](../tables/w02-matched.tex), including both charges, concentration diagnostics, synthetic endpoints and finite-change terms. The mechanism drawing remains in [w02-five-node.pdf](../figures/w02-five-node.pdf). These are retained source assets, not a separately published supplement. The table's historical `eq:change` reference denotes the identity below; the table is no longer included in the main document.

## Synthetic mechanism

The specified five-node graph has arcs a→b, b→c, c→a, a→d, d→e and e→b. Reversing the triangle preserves W, degrees, reciprocity, partitions and trophic incoherence. At q=.25 the triangle's flux cosine is unchanged, while the four-cycle sharing edge a–b changes flux from π to 2π. The first eigenvalue changes from approximately .2047 to .0685. This synthetic change is much larger than the selected documentation changes and is not a documentation effect estimate.

## Localization and finite-change account

At q=.25 the baseline modes are concentrated around Docker's Engine API reference, React's team page and security-advisory posts, and GitHub's enterprise-onboarding pages. GitHub's selected triangles lie away from its concentrated mode and carry little baseline energy. In Docker and React, the two triangles carrying at least their weight share of energy give the two largest responses. This is a descriptive pattern among the selected cycles, not a general response rule.

For a simple unit ground mode u_A, the exact identity is

\[
\lambda_1^B-\lambda_1^A
=u_A^*(\mathcal L_B-\mathcal L_A)u_A
+(\lambda_1^B-u_A^*\mathcal L_Bu_A).
\]

The first term holds the baseline mode fixed; the second allows relaxation and is nonpositive by the Rayleigh principle. In Docker cycle 1, a positive fixed-mode term is almost cancelled by relaxation. On the forest control they cancel completely. The identity passes on all 42 computed pair-charge rows; the nine global-reversal rows omit this account. These whole-intervention terms are not independent causal effects of individual links.

Total-variation distance in the detailed table compares page mass m with degree mass d/Σd. Effective support is 1/Σm² and is not a literal count of nonzero entries.

Authority: [Sprint 23 report](../../context/reports/2026-10-25-magnetic-structural-attribution.md), [frozen evidence](../../context/evidence/magnetic-attribution-23-v1-2026-10-25.json), and the historical summary provenance in Git before the October 31 adoption. Rebuild the current 23b summary with `python scripts/prepare_cycle_summary.py`. The historical full table uses `prepare_results_tables.py`; the mechanism figure uses `prepare_w02_displays.py`. Those broader generators also regenerate other displays and should not be run merely to update the summary.
