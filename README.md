# DocSpectraICSC2027

Study code, configurations and evidence records for *Measuring Directed Documentation Structure with Normalized Magnetic Spectra* (Rocker D'Antonio, Mississippi State University), prepared for ICSC 2027. The repository holds the drivers that ran each analysis, the prompts and configurations they used, and the evidence files that record every number the paper reports, each with its SHA-256.

Three repositories make up the study's artifacts:

| Repository | Role |
| --- | --- |
| [DocSpectra/RetrievalArena](https://github.com/DocSpectra/RetrievalArena) | reusable framework: graph operators, magnetic spectra, pooled support verification, scoring |
| [DocSpectra/DockerDocsSet2026](https://github.com/DocSpectra/DockerDocsSet2026) | the Docker documentation versions (original, LT, R0), question sets and support judgments |
| DocSpectra/DocSpectraICSC2027 (this repository) | study-specific drivers, configurations, protocol notes and evidence records |

## Layout

Files are copied byte for byte from the study workspace and keep their workspace paths. Evidence records pin the exact bytes of the scripts and prompts they used, and every driver locates the workspace root from its own path (`git/context/scripts/x.py` → repository root), so moving or editing a file would break both. `.gitattributes` disables line-ending conversion.

| Path | Contents |
| --- | --- |
| `git/context/scripts/` | drivers and their tests |
| `git/context/evidence/` | evidence records: inputs, plans, results, author-review verdicts; most have a `.sha256` sidecar |
| `git/context/configs/`, `git/context/prompts/`, `git/context/design/` | configurations, editor prompts, generator, scorer and judge prompts and schemas |
| `git/context/sandbox/` | the editor session and container runtime used for the refinement run |
| `git/context/reports/` | generated result tables that the drivers' `verify` steps compare byte for byte |
| `git/paper/` | display generators and their provenance ledgers |
| `artifacts/` | two figure inputs read by the display generators |
| `vendor/docspectra_importer/` | the historical exporter used for page discovery, document IDs, FAQ extraction and links, byte-identical to its pinned SHA-256 values |
| [`docs/protocol-notes.md`](docs/protocol-notes.md) | the refinement run as it was run: amendments, fixes, departures and sensitivity analyses |
| [`docs/results-map.md`](docs/results-map.md) | which driver and evidence file stand behind each reported result |

Some workspace paths and internal names carry labels from earlier internal projects (for example the directory `docker-cikm-2026-05-14` holding the recovered Docker export). Files named `*-preregistration-*` hold the study protocol as frozen internally before each run; nothing was registered publicly.

## Checking the records

```bash
python tools/check_manifest.py
```

This checks every file against `MANIFEST.sha256` and every `.sha256` sidecar in `git/context/evidence/`, using only the standard library. The paper's tables can be regenerated from the evidence alone:

```bash
mkdir -p git/paper/tables
python git/paper/scripts/prepare_results_tables.py
git diff --stat git/paper/results-table-provenance.json
```

It asserts the SHA-256 of its five source records, writes five tables to `git/paper/tables/` and rewrites `results-table-provenance.json`, which records each table's SHA-256; an empty `git diff` means the regenerated tables match the recorded ones.

Several drivers also have a `verify` or `check` step that recomputes a result from saved run records with no model calls (`wc_analysis_20.py verify`, `lt_trace_audit_24.py verify`, `tier1_completeness_25.py verify`, `normalized_magnetic_22.py check`, `magnetic_attribution_23.py check`, `wc_spectra_18.py check`). Those steps read the run records under `artifacts/` (answers, scores, traces and analysis intermediates) and the RetrievalArena checkout at the pinned commits; the run records are not part of this repository. Steps that answer, judge or edit (`wc_answers_19.py`, `wc_rejudge_19.py escalate`, `c2_pooled_qrels.py`, `wc_refine_17.py run`) call models or editing sessions and are documented for provenance, not for offline reruns.

## Dependencies

Python 3.12 was used. [`requirements.txt`](requirements.txt) lists the third-party packages: NumPy, SciPy and jsonschema at the versions installed on the build workstation; Matplotlib and ReportLab for figures, whose versions were not recorded; and the OpenAI client for the live answering and scoring stages only. RetrievalArena is used at the commits recorded in each evidence file.

## License

The study code, configurations, tools and documentation are released under the [MIT License](LICENSE) (Copyright 2026 Rocker D'Antonio). Some evidence records, prompts and generation records quote Docker documentation, which remains under Docker's Apache License 2.0; the files under `vendor/docspectra_importer/` are the author's own code, also under the MIT License.

## Citation

Cite this repository (version 1.0.0, Git tag `v1.0.0`) with [`CITATION.cff`](CITATION.cff). Cite the dataset as DockerDocsSet2026 and the framework as RetrievalArena separately.
