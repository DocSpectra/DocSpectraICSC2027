"""Sprint 11: move an editor tree between the host and a session volume as tar.

The host never opens filesystem objects the editor container created. Snapshots
arrive as a tar stream from `docker cp` of the paused container; only regular
files and directories with plain relative POSIX names are accepted. Symlinks,
hard links, devices, FIFOs and any name with '..', a drive or an absolute root
make the snapshot invalid. An invalid snapshot is reported, never repaired.
"""
from __future__ import annotations

import io
import shutil
import tarfile
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 20000


class TreeViolation(Exception):
    """The editor tree holds something the host will not materialize."""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}:{detail}" if detail else kind)
        self.kind, self.detail = kind, detail


def clean_name(name: str) -> str | None:
    """'./a/b.md' -> 'a/b.md'; '.' -> ''; None for anything unsafe."""
    if "\\" in name or ":" in name or "\x00" in name:
        return None
    path = PurePosixPath(name)
    if path.is_absolute():
        return None
    parts = [p for p in path.parts if p != "."]
    if any(p in ("..", "") for p in parts):
        return None
    return "/".join(parts)


def read_snapshot(stream) -> dict[str, bytes]:
    """Parse a `docker cp <c>:/work/. -` tar stream into {relative path: bytes}."""
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            rel = clean_name(member.name)
            if rel is None:
                raise TreeViolation("unsafe-name", member.name[:200])
            if member.isdir():
                continue
            if not member.isreg():
                kind = ("symlink" if member.issym() else "hardlink" if member.islnk() else
                        "fifo" if member.isfifo() else "device" if member.isdev() else f"type-{member.type!r}")
                raise TreeViolation("nonregular-" + kind, rel)
            if rel in files:
                raise TreeViolation("duplicate-name", rel)
            if member.size > MAX_FILE_BYTES:
                raise TreeViolation("file-too-large", rel)
            if len(files) >= MAX_FILES:
                raise TreeViolation("too-many-files")
            files[rel] = archive.extractfile(member).read()
    return files


def write_tar(files: dict[str, bytes], uid: int, gid: int) -> bytes:
    """A deterministic tar of regular files owned by the editor uid (for seeding a volume)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        dirs = sorted({str(PurePosixPath(rel).parent) for rel in files} - {"."})
        expanded = set()
        for d in dirs:
            parts = d.split("/")
            expanded.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        for d in sorted(expanded):
            info = tarfile.TarInfo(d)
            info.type, info.mode, info.uid, info.gid, info.mtime = tarfile.DIRTYPE, 0o755, uid, gid, 0
            archive.addfile(info)
        for rel in sorted(files):
            if clean_name(rel) != rel:
                raise TreeViolation("unsafe-name", rel)
            data = files[rel]
            info = tarfile.TarInfo(rel)
            info.size, info.mode, info.uid, info.gid, info.mtime = len(data), 0o644, uid, gid, 0
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def read_host_tree(root: Path) -> dict[str, bytes]:
    """Host-side tree (written only by host code) as {relative path: bytes}."""
    root = root.resolve(strict=True)
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise TreeViolation("host-tree-link", str(path))
        if path.is_file():
            out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def mirror(files: dict[str, bytes], root: Path) -> None:
    """Make host `root` hold exactly `files` (host-written bytes only)."""
    root = root.resolve(strict=True)
    current = read_host_tree(root)
    for rel in sorted(set(current) - set(files)):
        (root / rel).unlink()
    for rel, data in sorted(files.items()):
        target = root / rel
        if root not in target.resolve().parents:
            raise TreeViolation("mirror-escape", rel)
        if current.get(rel) != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    for directory in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(directory.iterdir()):
            directory.rmdir()


def diff(before: dict[str, bytes], after: dict[str, bytes]) -> dict[str, list[str]]:
    return {"added": sorted(set(after) - set(before)), "deleted": sorted(set(before) - set(after)),
            "modified": sorted(k for k in set(before) & set(after) if before[k] != after[k])}


def edit_count(d: dict[str, list[str]]) -> int:
    """Edits between two snapshots: one per added, deleted or modified path."""
    return len(d["added"]) + len(d["deleted"]) + len(d["modified"])


def remove_tree(root: Path) -> None:
    shutil.rmtree(root)
