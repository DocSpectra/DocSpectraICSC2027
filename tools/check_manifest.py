"""Check DocSpectraICSC2027 files against MANIFEST.sha256 and the evidence .sha256 sidecars (standard library only)."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    failures = 0
    listed = {}
    for line in (ROOT / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        listed[name] = digest
    bad = [n for n, d in listed.items() if not (ROOT / n).is_file() or sha256(ROOT / n) != d]
    print(f"{'PASS' if not bad else 'FAIL'} MANIFEST.sha256: {len(listed) - len(bad)}/{len(listed)} files match" + (f" (first bad: {bad[0]})" if bad else ""))
    failures += bool(bad)
    sidecars = sorted((ROOT / "git" / "context" / "evidence").glob("*.sha256"))
    checked, mismatched = 0, []
    for sidecar in sidecars:
        for line in sidecar.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parts = line.split(None, 1)
            # A sidecar holds "<sha256>  <file>" lines, or a bare hash for the same-named .json record.
            digest, name = (parts[0], sidecar.with_suffix(".json").name) if len(parts) == 1 else parts
            target = sidecar.parent / name.lstrip("*").strip()
            if not target.is_file():
                target = ROOT / name.lstrip("*").strip()
            checked += 1
            if not target.is_file() or sha256(target) != digest:
                mismatched.append(f"{sidecar.name}: {name.strip()}")
    print(f"{'PASS' if not mismatched else 'FAIL'} evidence sidecars: {checked - len(mismatched)}/{checked} entries match" + (f" (first bad: {mismatched[0]})" if mismatched else ""))
    failures += bool(mismatched)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
