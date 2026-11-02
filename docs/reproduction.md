# Offline reproduction

Extract the source ZIP/bundle and saved-input ZIP together. Verify both archive
checksums against the external release receipt before extraction. The source
bundle preserves v1.0.0 history; no old release tag is moved. The separate RA
bundle contains the exact release/verifying commit and its historical ancestors.
The dataset v1.0.0 bundle and assets remain unchanged dependencies.

## Supported commands

`python tools/verify_submission.py manifest` verifies source and saved-input
manifests, evidence sidecars, file sizes/hashes in the submission manifest and
absence of path escapes. `saved` checks 23b selection ledgers, receipts,
input/output hashes, saved pair fields, primary counts and derived artifacts;
it also checks the fresh blinded support inventory against recorded responses
and the source/protocol identities. These do not solve new eigenproblems.

`historical` calls the unchanged historical verifiers for 20, 24, 25 and 22
through a portable wrapper. It supplies the bundled, hash-checked importer
instead of requiring its old workstation location. It confines reads to this
unpacked study and interpreter dependencies and rejects network access.
No API keys or model clients are used.

`example --output NEW_DIRECTORY` invokes the RA `blinded-support-verify` stage
on the complete saved input, reconstructs all four 897-file copies and patches,
checks externally anchored ledgers, and reruns the changed eleven-candidate
tier using the frozen finite solver and minimum-set rule. Numerical equality
is strict under the recorded image; other BLAS/platform versions may require
explicitly reported tolerances rather than silently changing expected records.

The recorded 41c verification image is pinned in
`git/context/configs/blinded-support-review-41c/verification-receipt-v1.json`.
Its source is `e77f87b313a151483c0900c57a18cd45440290cd`. With that image already
installed, mount the unpacked study at `/study:ro` and a fresh writable directory
at `/out`, use `--network none --read-only --cpus 2 --memory 2g`, a writable
`/tmp` tmpfs and `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1`:

```sh
docker run --rm --network none --read-only --cpus 2 --memory 2g \
  --tmpfs /tmp -e OPENBLAS_NUM_THREADS=1 -e OMP_NUM_THREADS=1 -e MKL_NUM_THREADS=1 \
  -v "$PWD:/study:ro" -v "/absolute/new-output:/out" \
  --entrypoint python IMAGE_ID /study/tools/verify_submission.py example --output /out/replay
```

Use the image ID/reference from that receipt, not a mutable latest tag.
Readers who do not have the image can build the container from the bundled RA
source and lock using RA's documented image builder; downloading dependencies
is separate from the offline verification. No hosted image availability is
promised. The host route uses Python 3.12, NumPy, SciPy, jsonschema and RA's
requirements; it is a convenience route, not the historical environment.

## Paper displays and build

With ReportLab installed, `python tools/verify_submission.py displays` regenerates
five numerical tables, the expanded cycle summary and both included measured
plots, comparing outputs with the frozen files. All numerical values must agree;
PDF metadata may differ across ReportLab builds, so content identity and byte
identity are reported separately. The omitted conceptual LT picture is retained
as a historical asset, not a paper requirement.

Build `git/paper/main.tex` with MiKTeX/TeX Live, BibTeX and IEEEtran:

```sh
cd git/paper
mkdir -p build
pdflatex -interaction=nonstopmode -halt-on-error -output-directory=build main.tex
bibtex build/main
pdflatex -interaction=nonstopmode -halt-on-error -output-directory=build main.tex
pdflatex -interaction=nonstopmode -halt-on-error -output-directory=build main.tex
```

The reference PDF is eight pages, with five tables, two figures and 27 cited
works. Local TeX versions can alter pagination; inspect the output visually.
The exact delivered PDF hash is in the external receipt, avoiding a self-hash
cycle with the source commit and package manifest.

## Historical commands and excluded scope

Byte-pinned old scripts remain for provenance. Their `run`, `launch`, `measure`,
`judge`, generation and editing commands are not offline reproduction commands.
Original source-preservation checks may intentionally refer to the then-current
paper or context Git history; use this release's wrapper for current-source
reproduction. No entire private context history is exported. Chronology proofs
are exported as file bytes, commit IDs, timestamps and object hashes.

The full legacy test discovery had known historical failures. The supported
release checks and their observed results are recorded separately; no blanket
claim that every past experiment can be rerun is made. Later unadopted research
and future Sprint 43 are outside this submission.
