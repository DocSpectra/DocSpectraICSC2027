# Results and submission reconciliation

| Paper result/display | Evidence | Exact record hash | Check |
|---|---|---|---|
| Section III | [measurement](../git/context/evidence/normalized-magnetic-22-v1-2026-10-25.json) | `47bad4ea0a8132ef0e37b6a09a0627a2bdb5e139e35bf171f8097397b8431e3e` | `python tools/verify_submission.py historical` |
| Section IV; Table I | [reversals](../git/context/evidence/magnetic-attribution-23b-run-v1-2026-10-31.json) | `934ee3a187d9fb5e50696637d14c0d4c7e1be2e1dc9992d90b9844ff81fb8be7` | `python tools/verify_submission.py saved` |
| Section V; Tables II/III | [characterization](../git/context/evidence/normalized-magnetic-22-v1-2026-10-25.json) | `47bad4ea0a8132ef0e37b6a09a0627a2bdb5e139e35bf171f8097397b8431e3e` | `python tools/verify_submission.py displays` |
| Table IV; Figure 1 | [refinement-spectra](../git/context/evidence/normalized-magnetic-22-v1-2026-10-25.json) | `47bad4ea0a8132ef0e37b6a09a0627a2bdb5e139e35bf171f8097397b8431e3e` | `python tools/verify_submission.py historical` |
| Section VI-C; Figure 2 | [downstream](../git/context/evidence/wc-analysis-20-v1-2026-10-24.json) | `aeaac4758eaa6a66f9fa68ea6ee3cd398fe87f23b9456700f8a02e420aa12a23` | `python tools/verify_submission.py historical` |
| Table V; Section VI-C | [link-use](../git/context/evidence/lt-trace-audit-24-v1-2026-10-25.json) | `9ce25cdcb48d979a2107a91ea62bc21a3a1cfc60df5859f2ddb7a6ed1f91ef65` | `python tools/verify_submission.py historical` |
| Section VI-D; equation 6 | [worked-example](../git/context/evidence/blinded-support-review-41c-real-v1-2026-11-02.json) | `0dc67bbf0d7261f7a5e9e39c5f6c5a60736c096ac92dbfec7b2cfc42586e3e80` | `python tools/verify_submission.py example --output NEW_DIRECTORY` |
| Appendix; Section VI-B | [evaluation-checks](../git/paper/review-calibration-provenance.json) | `2ab2cab2ac401de09fc157e21b5213aaa82894d426d30c4ed604a97fdb81a937` | `python tools/verify_submission.py manifest` |

The source-file allowlist records every code/data dependency and its package owner. Original numerical/model settings and historical revisions remain in the linked evidence. The external receipt distinguishes producing, replay, source and release identities.

The detailed example record is reproduction documentation; no separate manuscript note is required. Public access remains pending until remote visibility and assets are independently checked.
