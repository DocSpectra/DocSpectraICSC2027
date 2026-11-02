# Sprint 23b: matched magnetic structural-attribution coverage

October 31, 2026. Offline exploratory study; zero model/outbound calls. Evidence: [run JSON](../evidence/magnetic-attribution-23b-run-v1-2026-10-31.json).

All fourteen frozen snapshots are accounted for. 77 cycles were selected in 12 corpora; 77 pass the matched controls and 77 qualify numerically at q=0.25. 54 changes resolve in 10 corpora.

## Coverage and primary responses

S/M/Q/R = selected/matched/qualified/resolved. Search entries for lengths 3/4/5/6 give candidate count plus E (exhaustive) or C (capped). Each length has an independent 250,000-expansion budget and two-cycle quota. Signed ranges and median absolute deltas include unresolved pairs.

| Corpus | Full→view nodes (coverage) | Candidates/status 3/4/5/6 | S/M/Q/R | Signed Δ median [Q1,Q3]; range | Absolute Δ median [Q1,Q3]; range | Effective support fraction |
|---|---|---|---|---|---|---|
| agopengps | 59→23 (39.0%) | 1E / 0E / 0E / 0E | 1/1/1/1 | 0.00536 [0.00536,0.00536]; 0.00536…0.00536 | 0.00536 [0.00536,0.00536]; 0.00536…0.00536 | 50.30% |
| ansible_docs | 492→391 (79.5%) | 3E / 3E / 0E / 1E | 5/5/5/1 | 1.22e-09 [5.87e-11,1.26e-09]; 2.85e-11…9.58e-08 | 1.22e-09 [5.87e-11,1.26e-09]; 2.85e-11…9.58e-08 | 2.14% |
| argo_cd_docs | 470→393 (83.6%) | 5E / 0E / 1E / 2E | 5/5/5/5 | 1.48e-06 [1.48e-07,2.07e-06]; -8.11e-08…2.58e-06 | 1.48e-06 [1.48e-07,2.07e-06]; 8.11e-08…2.58e-06 | 7.48% |
| django_docs | 651→649 (99.7%) | 16E / 32E / 34E / 79E | 8/8/8/8 | 1.26e-05 [7.99e-06,6.52e-05]; 3.28e-07…0.000295 | 1.26e-05 [7.99e-06,6.52e-05]; 3.28e-07…0.000295 | 0.68% |
| docker_docs | 1263→844 (66.8%) | 38E / 36E / 53E / 111E | 8/8/8/8 | 1.09e-06 [7.12e-07,2.21e-06]; -8.95e-07…1.3e-05 | 1.09e-06 [9.4e-07,2.21e-06]; 1.49e-08…1.3e-05 | 0.59% |
| elasticsearch_docs | 2364→2257 (95.5%) | 254E / 415E / 1094E / 960C | 8/8/8/4 | -8e-11 [-2.96e-09,4.96e-09]; -5.29e-08…5.53e-08 | 5.98e-09 [1.8e-10,2.64e-08]; 6.54e-11…5.53e-08 | 0.18% |
| express_docs | 52→44 (84.6%) | 0E / 0E / 0E / 0E | 0/0/0/0 | NA [NA,NA]; NA…NA | NA [NA,NA]; NA…NA | 12.97% |
| github_docs | 3515→2354 (67.0%) | 44E / 63E / 69E / 111E | 8/8/8/0 | 1.87e-11 [-1.8e-11,3.77e-10]; -5.67e-09…4.97e-10 | 1.96e-10 [3.09e-11,4.75e-10]; 7.47e-13…5.67e-09 | 0.29% |
| kubernetes_website_docs | 1572→1146 (72.9%) | 80E / 181E / 538E / 1753E | 8/8/8/8 | 9.35e-07 [-6.59e-10,1.52e-06]; -4.02e-06…4.47e-06 | 1.19e-06 [5.7e-07,2.87e-06]; 6.64e-08…4.47e-06 | 1.11% |
| nextjs_docs | 418→295 (70.6%) | 48E / 194E / 659E / 2259E | 8/8/8/8 | 1.12e-06 [-9.1e-07,4.45e-06]; -1.75e-05…6.78e-06 | 3.62e-06 [1.21e-06,6.22e-06]; 1.04e-07…1.75e-05 | 2.45% |
| pytorch_docs | 244→9 (3.7%) | 0E / 0E / 0E / 0E | 0/0/0/0 | NA [NA,NA]; NA…NA | NA [NA,NA]; NA…NA | 62.07% |
| react_docs | 218→206 (94.5%) | 39E / 106E / 279E / 808E | 8/8/8/8 | 5.21e-05 [8.21e-06,0.000129]; -5.54e-06…0.00086 | 5.21e-05 [9.73e-06,0.000129]; 5.31e-07…0.00086 | 9.07% |
| redis_docs | 97→45 (46.4%) | 1E / 1E / 0E / 0E | 2/2/2/0 | 7.98e-17 [4.25e-17,1.17e-16]; 5.2e-18…1.54e-16 | 7.98e-17 [4.25e-17,1.17e-16]; 5.2e-18…1.54e-16 | 17.68% |
| vscode_docs | 770→658 (85.5%) | 60E / 116E / 443E / 1445E | 8/8/8/3 | 4.63e-09 [-5.17e-10,5.32e-07]; -3.5e-09…4.25e-06 | 6.4e-09 [9.26e-10,5.32e-07]; 3.49e-11…4.25e-06 | 0.55% |

## Magnitude and localization interpretation

The largest selected relative response is 7.03% on agopengps's 23-node view (39.0% of its snapshot). Its baseline effective support is 50.3% of that view. Across the other 9 corpora with resolved responses, the largest selected relative response is 0.554%; their baseline modes have effective support below 10% of the view. Thus the broader study extends the named examples beyond Docker/React, including one less concentrated case, while most resolved responses remain small and localized. One selected cycle on a small view does not establish a typical response or practical importance.

Giving each of the 12 eligible corpora equal weight, the mean within-corpus resolved fraction is 67.3% at q=.25 and 87.3% at q=.10. These are descriptive summaries of the selected frames, not estimates of prevalence in documentation corpora.

## Fixed q=0.10 companion

| Corpus | Qualified/resolved | Signed Δ median [Q1,Q3]; range | Absolute Δ median [Q1,Q3]; range |
|---|---|---|---|
| agopengps | 1/1 | 0.000293 [0.000293,0.000293]; 0.000293…0.000293 | 0.000293 [0.000293,0.000293]; 0.000293…0.000293 |
| ansible_docs | 5/3 | 8.88e-09 [3.39e-09,4.6e-08]; -3.75e-08…2.49e-07 | 3.75e-08 [8.88e-09,4.6e-08]; 3.39e-09…2.49e-07 |
| argo_cd_docs | 5/5 | -3.83e-07 [-6.11e-07,4.05e-07]; -7.88e-06…4.2e-07 | 4.2e-07 [4.05e-07,6.11e-07]; 3.83e-07…7.88e-06 |
| django_docs | 8/8 | -4.57e-07 [-2.05e-05,1.18e-05]; -9.56e-05…5.35e-05 | 1.51e-05 [1.01e-05,4.86e-05]; 9.41e-06…9.56e-05 |
| docker_docs | 8/8 | 4.14e-06 [-1.77e-06,5.52e-06]; -2.89e-06…3.18e-05 | 4.14e-06 [2.64e-06,5.52e-06]; 1.72e-06…3.18e-05 |
| elasticsearch_docs | 8/7 | -9.49e-08 [-1.19e-06,2.84e-06]; -1.42e-05…5.98e-06 | 2.85e-06 [4.89e-07,3.75e-06]; 3.28e-09…1.42e-05 |
| express_docs | 0/0 | NA [NA,NA]; NA…NA | NA [NA,NA]; NA…NA |
| github_docs | 8/0 | 4.52e-10 [-5.32e-10,9.6e-10]; -1e-09…2.32e-09 | 7.56e-10 [5.68e-10,1.11e-09]; 1.91e-10…2.32e-09 |
| kubernetes_website_docs | 8/8 | 8.84e-07 [-5.27e-07,2.96e-06]; -1.38e-06…8.02e-06 | 1.51e-06 [9.2e-07,2.96e-06]; 1.32e-07…8.02e-06 |
| nextjs_docs | 8/8 | 5.5e-06 [-1.98e-06,1.44e-05]; -6.9e-05…2.1e-05 | 9.59e-06 [4.67e-06,1.98e-05]; 1.08e-06…6.9e-05 |
| pytorch_docs | 0/0 | NA [NA,NA]; NA…NA | NA [NA,NA]; NA…NA |
| react_docs | 8/8 | 8.68e-06 [2.3e-06,2.56e-05]; -0.000113…6.92e-05 | 1.58e-05 [4.78e-06,5.01e-05]; 7.69e-07…0.000113 |
| redis_docs | 2/2 | -0.000134 [-0.000149,-0.000119]; -0.000164…-0.000104 | 0.000134 [0.000119,0.000149]; 0.000104…0.000164 |
| vscode_docs | 8/8 | -1.09e-06 [-1.22e-05,4.11e-06]; -2.53e-05…1.13e-05 | 6.8e-06 [4.01e-06,1.35e-05]; 1.57e-06…2.53e-05 |

The primary signed medians/IQRs and absolute ranges, relative distributions, full per-pair diagnostics and expansion counts are in the evidence and all-pair JSON. Relative changes are unavailable when the baseline is below its resolution floor. Lambda2 is retained as a descriptive ordered-eigenvalue companion; multiplicity and crossings prevent interpreting it as a tracked physical mode.

Both charges resolve 54 pairs; 22 of those disagree in sign. No q was selected by outcome.

## Controls, qualification and accounting

All q=0 matched deltas are exactly zero after explicit matrix-equality reuse: True. Global reversal structural/numerical/invariance controls pass across all fourteen: True. Baselines reproduce Sprint 22 within 0; the historical three-anchor replay reproduced every old cycle and control, and its spectra within the recorded 1e-12 tolerance.

Worst ground residual 9.49e-15; mass-sum maximum error 2.66e-15. Each of the lowest three modes has an absolute residual bounded by the saved full-eigenbasis Frobenius residual (worst bound 7.13e-13); individual lambda2/lambda3 residuals were not exported. Exact ground-cluster residuals govern resolution. Energy identity failures: 0. Finite-change accounts: 231 computed, 0 unavailable, 0 identity/relaxation failures (all charges). A clustered or unqualified ground mode omits the scalar account and retains projector diagnostics; these accounts do not identify independent causal effects per hyperlink.

Selected overlap with the original twelve interventions: 0 previously examined, 77 newly examined. The overlap companion is descriptive; neither subset is independent confirmation. No pair was replaced after outcome inspection.

Searches sample encountered candidates. Capped-empty cells mean none found within the search budget; exhaustive-empty cells mean no eligible cycle of that length. No unused quota transfers. Fourteen convenience snapshots and multiple edits sharing one graph do not justify a pooled significance test. Equal-corpus summaries state their eligible denominator in the JSON.

## Localization and case cards

The figure plots every qualified primary pair against baseline cycle mass and energy, preserving corpus identity and unresolved responses. Mass/energy are diagnostics measured after selection; localized support can explain insensitivity to some edits but does not prove every null response. Cycle length, degree-volume share and edited W share are retained per pair. Projector fields describe the complete ground cluster; no arbitrary vector in a repeated eigenspace is interpreted.

![All responses and baseline localization](../../../artifacts/magnetic-attribution-23b-v1/figures/response-localization.png)

Cards use the first frozen length/selection-hash cycle for each corpus. The JSON records original IDs/arcs and all attribution fields. No source-page interpretation was performed.

- **agopengps**: length 3, mass 0.129, edge energy 0.0163, Δ 0.00536, resolved True; IDs/arcs in `analysis/case-cards.json`.
- **ansible_docs**: length 3, mass 1.54e-06, edge energy 1.41e-07, Δ 9.58e-08, resolved True; IDs/arcs in `analysis/case-cards.json`.
- **argo_cd_docs**: length 3, mass 1.12e-06, edge energy 1.08e-07, Δ -8.11e-08, resolved True; IDs/arcs in `analysis/case-cards.json`.
- **django_docs**: length 3, mass 0.477, edge energy 0.00195, Δ 0.000295, resolved True; IDs/arcs in `analysis/case-cards.json`.
- **docker_docs**: length 3, mass 1.66e-06, edge energy 1.7e-07, Δ -1.49e-08, resolved True; IDs/arcs in `analysis/case-cards.json`.
- **elasticsearch_docs**: length 3, mass 3.18e-09, edge energy 3.81e-10, Δ -9.46e-11, resolved False; IDs/arcs in `analysis/case-cards.json`.
- **express_docs**: no eligible selected case; see exhaustive/capped coverage above.
- **github_docs**: length 3, mass 1.64e-08, edge energy 1.01e-09, Δ 4.68e-10, resolved False; IDs/arcs in `analysis/case-cards.json`.
- **kubernetes_website_docs**: length 3, mass 6.09e-05, edge energy 3.97e-06, Δ 6.64e-08, resolved True; IDs/arcs in `analysis/case-cards.json`.
- **nextjs_docs**: length 3, mass 0.000686, edge energy 2.54e-05, Δ 3.92e-06, resolved True; IDs/arcs in `analysis/case-cards.json`.
- **pytorch_docs**: no eligible selected case; see exhaustive/capped coverage above.
- **react_docs**: length 3, mass 0.0013, edge energy 0.000108, Δ 1.11e-05, resolved True; IDs/arcs in `analysis/case-cards.json`.
- **redis_docs**: length 3, mass 0.000293, edge energy 2.98e-05, Δ 5.2e-18, resolved False; IDs/arcs in `analysis/case-cards.json`.
- **vscode_docs**: length 3, mass 3.4e-08, edge energy 2.16e-09, Δ -3.12e-10, resolved False; IDs/arcs in `analysis/case-cards.json`.

## Manuscript-inclusion proposal (author decision pending)

Replace the three-anchor matched-reversal table with the compact table below; retain at most the localization figure in the supporting artifact unless space permits. Proposed wording: ‘Across fourteen frozen documentation snapshots, topology-only sampling selected 77 individual cycle reversals in 12 corpora. At q=0.25, 54 changes in 10 corpora exceeded the numerical resolution threshold while preserving the weighted undirected graph, directed degrees, component partitions and trophic incoherence. Effect magnitudes and ground-mode localization varied by corpus. These exploratory observations establish sensitivity beyond the specified controls on the observed pairs; they do not establish practical usefulness.’ Include the observed ranges from the table, rather than presenting resolution as practical importance.

| Corpus | Selected/resolved (.25) | Absolute Δ median; maximum | Maximum absolute relative change | Baseline effective support |
|---|---|---|---|---|
| agopengps | 1/1 | 0.00536; 0.00536 | 7.03% | 50.30% |
| ansible_docs | 5/1 | 1.22e-09; 9.58e-08 | 0.000728% | 2.14% |
| argo_cd_docs | 5/5 | 1.48e-06; 2.58e-06 | 0.0675% | 7.48% |
| django_docs | 8/8 | 1.26e-05; 0.000295 | 0.554% | 0.68% |
| docker_docs | 8/8 | 1.09e-06; 1.3e-05 | 0.0155% | 0.59% |
| elasticsearch_docs | 8/4 | 5.98e-09; 5.53e-08 | 5.73e-05% | 0.18% |
| express_docs | 0/0 | NA; NA | NA | 12.97% |
| github_docs | 8/0 | 1.96e-10; 5.67e-09 | 4.06e-05% | 0.29% |
| kubernetes_website_docs | 8/8 | 1.19e-06; 4.47e-06 | 0.00886% | 1.11% |
| nextjs_docs | 8/8 | 3.62e-06; 1.75e-05 | 0.0135% | 2.45% |
| pytorch_docs | 0/0 | NA; NA | NA | 62.07% |
| react_docs | 8/8 | 5.21e-05; 0.00086 | 0.41% | 9.07% |
| redis_docs | 2/0 | 7.98e-17; 1.54e-16 | 1.25e-12% | 17.68% |
| vscode_docs | 8/3 | 6.4e-09; 4.25e-06 | 0.00685% | 0.55% |

Adoption map: replace `git/paper/tables/w02-matched-summary.tex`; revise the sampling paragraph in `sections/03-evaluation-design.tex` and the second subsection of `sections/04-structural-validation.tex` together. Preserve the approved introduction. No page-fit result is claimed because this sprint does not modify or compile the manuscript.

The original Sprint 23 conclusion remains scoped to its selected Docker/React/GitHub triangles. This extension uses a different encountered candidate frame and several cycle lengths; differences between studies are not a controlled selection-method comparison. Full old/new accounting remains in the artifact. Keep the negative Sprint 31c AI-purpose result there as directed, without a page-purpose paper section. No submitted manuscript, bibliography, tag or public artifact was changed.

## Resources and reproduction

RA `38c8ed55c645a3a67761969c566d625d626634a3`; image `sha256:1535e4af8b2514e64e2acccdfac96426f63bb7093c4ea2c145af59b20a78f4d6`. New real-graph measure time 687.1 seconds, generated files at analysis 231.9 MiB. Containers use two CPUs, 4 GiB and single-threaded BLAS, network disabled. Per-run wall times, module/input/output hashes and runtime versions are in the JSON. Host and container qualification are distinct; no arm64/macOS/HPC run is claimed.

Commands: `python -B git/context/scripts/magnetic_attribution_23b.py qualify-inputs`, `qualify-run`, `freeze`; commit plan and selection, then `measure`. Run this reporting script in the pinned image. Measurement resumes only from verified complete receipts. Historical inputs and the operator module remain byte-identical, checked in the evidence.
