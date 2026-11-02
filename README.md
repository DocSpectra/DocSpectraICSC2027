# DocSpectraICSC2027 1.1.0

Reproduction sources for *Measuring Directed Documentation Structure with
Normalized Magnetic Spectra*, by Rocker D'Antonio and Dimitrios Michael Manias,
Mississippi State University, prepared for ICSC 2027. The submitted scientific
account is one eight-page paper. No companion manuscript is required.

This version supersedes 1.0.0 for reproducing the November 2 manuscript. It adds
the adopted fourteen-corpus cycle-reversal study (23b), the thirteen-candidate
blinded AI-supported link-selection example (41c), current paper sources and
displays, and saved inputs for the previously incomplete offline verification.
It preserves the earlier producing-code identities and release history.

## Start here

Use the source bundle or source ZIP together with
`DocSpectraICSC2027-1.1.0-saved-inputs.zip`, extracted into the same root. Their
checksums and the precise manuscript PDF identity are in the external release
receipt. Extract `RetrievalArena-icsc2027-submission-1.1.0.bundle` to
`git/RetrievalArena` at `e77f87b313a151483c0900c57a18cd45440290cd`.
Do not substitute a globally installed package with the same name.

```sh
git clone DocSpectraICSC2027-1.1.0.bundle study
cd study
git config core.longpaths true
# Extract saved-inputs.zip here using an archive tool.
git -c core.autocrlf=false clone /path/to/RetrievalArena-icsc2027-submission-1.1.0.bundle git/RetrievalArena
python tools/verify_submission.py manifest
python tools/verify_submission.py saved
python tools/verify_submission.py historical
python tools/verify_submission.py example --output /path/to/new-output
```

`manifest` uses the standard library. `historical` replays the saved whole-corpus
analysis, trace audit, element comparison and normalized-profile preservation
checks. `example` checks all source copies and anchored edit ledgers and reruns
the changed candidate tier's finite numerical estimates. It creates a fresh
output directory. Run the example's numerical replay in the recorded verification image
with `--network none`, single-thread BLAS and the study mounted read-only; see
[reproduction](docs/reproduction.md). Historical analysis uses the declared host
requirements; the recorded image lacks its `jsonschema` dependency. These
verification commands never invoke provider clients.

[Results map](docs/results-map.md) connects every paper display/result family to
evidence and commands. [Submission manifest](submission/manifest.json) records
authorship, versions, evidence, historical/replay pins and limitations.
[Protocol notes](docs/protocol-notes.md) describe the historical refinement as run.
[Worked-example methods](docs/worked-example.md) retain the full candidate
inventory and methodology as reproduction documentation.

## Data and licenses

DockerDocsSet2026 remains version **1.0.0**, main
`ab4fb5636e71aa82ada557a8b0f7a27d1d02563a`, with unchanged `docs-original`,
`docs-lt`, `docs-r0`, questions and labels. This release's saved-input asset
additionally carries the actual graph bundles, masked example source view,
recorded AI assessments, patches, answers and scientific blinding maps needed by
the supported offline commands. The word `private` in an old scientific
blinding-map path describes its original experimental role; it is not an API
credential. No credentials, author correspondence or internal review PDFs are
included.

Study-authored code and documentation use [MIT](LICENSE). Docker documentation,
its excerpts and derived copies retain Apache-2.0; see [NOTICE](NOTICE.md) and
[Docker license](LICENSES/Docker-Apache-2.0.txt). The legacy importer is the
author's MIT code. Other corpus graph bundles contain document identifiers and
link topology, not redistributed documentation bodies. Upstream repository
identities and source/license boundaries are listed in the recorded input
manifests; no blanket license is asserted over third-party documentation.

## Limits

The cycle study is topology-selected exploratory evidence, with incomplete
coverage and numerical-resolution qualifications. The worked example has one
competitive changed group of four, exposed method development, eight tied
minima and blinded AI reference support. It supplies no human validation,
independent replication or tested access/answer benefit. Historical LT changes
text, page boundaries and links together, with one realization per arm. Failed
judging qualifications, retries, missing attempts and conditional intervals
remain recorded. Saved judgments are verifiable observations, not deterministic
regenerations of stochastic providers. Numerical stationarity is not a global
optimality certificate. See the result-specific qualifications in the paper.

Release identities and checksums are recorded in the external publication
receipt. Reader access depends on the repository visibility at retrieval.
The author controls the switch to public access at paper submission; private
releases require authorized repository access. No conference submission is
performed by these artifact tools.

Cite [CITATION.cff](CITATION.cff); cite DockerDocsSet2026 and RetrievalArena
separately. Paper authorship and software authorship are recorded separately.
