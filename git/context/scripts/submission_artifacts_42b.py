"""Build the allowlisted 42b release without network access or model calls.

Historical builders and evidence remain unchanged. This builder consumes a frozen
file list, extends the existing study history, and creates a separate saved-input
archive. All output roots must be new. No push, remote creation, or release API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[3]
CTX = ROOT / 'git/context'
VERSION = '1.1.0'
DATE = '2026-11-02'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False).encode() + b'\n')


def safe_path(root, name):
    part = PurePosixPath(name)
    if part.is_absolute() or '..' in part.parts or '\\' in name or ':' in name:
        raise ValueError('nonportable or escaping package path')
    target = root.joinpath(*part.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('package path escapes root')
    return target


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.STDOUT).decode().strip()


def pack_zip(path, files):
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, src in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 11, 2, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, src.read_bytes())
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(files)
        for name, src in files.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == sha(src), name


def augment_saved_archive(previous, path, rows):
    """Extend a verified saved-input ZIP in a new file, preserving its old bytes.

    Every member is checked against the frozen file-list hash, rather than a
    second reading of a potentially changing source workstation file.
    """
    by_path={r['path']:r for r in rows}
    assert len(by_path)==len(rows)
    done=set()
    with zipfile.ZipFile(previous) as old, zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as new:
        def member(name,raw):
            assert name not in done
            assert hashlib.sha256(raw).hexdigest()==by_path[name]['sha256'],name
            info=zipfile.ZipInfo(name,(2026,11,2,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            new.writestr(info,raw);done.add(name)
        for name in old.namelist():
            if name=='SAVED-INPUTS.sha256':continue
            member(name,old.read(name))
        for name in sorted(set(by_path)-done):member(name,safe_path(ROOT,by_path[name]['source']).read_bytes())
        assert done==set(by_path)
        manifest=''.join(f"{by_path[n]['sha256']}  {n}\n" for n in sorted(by_path)).encode()
        new.writestr('SAVED-INPUTS.sha256',manifest)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist())==done|{'SAVED-INPUTS.sha256'}
        for name,row in by_path.items():assert hashlib.sha256(archive.read(name)).hexdigest()==row['sha256'],name


def build(allowlist, out):
    if out.exists():
        raise ValueError('output root must be new')
    plan = json.loads(allowlist.read_bytes())
    # Validate the entire input before making a candidate tree.
    for row in plan['files']:
        assert sha(safe_path(ROOT, row['source'])) == row['sha256'], row['source']
        safe_path(out, row['path'])
    old = safe_path(ROOT, plan['previous_study_repo'])
    assert git(old, 'rev-parse', 'HEAD') == plan['previous_study_commit']
    assert not git(old, 'status', '--porcelain')
    out.mkdir(parents=True)
    repo = out / 'DocSpectraICSC2027'
    # Local clone preserves prior commits/tags; it does not contact GitHub.
    subprocess.run(['git', 'clone', '--local', '--no-hardlinks', '--no-checkout', str(old), str(repo)], check=True, capture_output=True)
    git(repo, 'config', 'core.autocrlf', 'false')
    git(repo, 'config', 'core.longpaths', 'true')
    # No checkout is made: the index is populated solely from explicit new files.
    assets = out / 'release-assets'; assets.mkdir()
    saved = out / 'saved-inputs'; saved.mkdir()
    for row in plan['files']:
        target = safe_path(repo if row['package'] == 'study' else saved, row['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe_path(ROOT, row['source']), target)
    templates = CTX / 'release/DocSpectraICSC2027-1.1.0'
    for src in sorted(p for p in templates.rglob('*') if p.is_file()):
        target = repo / src.relative_to(templates)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    write(repo/'submission/input-allowlist.json', plan)
    for folder, name in ((repo, 'MANIFEST.sha256'), (saved, 'SAVED-INPUTS.sha256')):
        files = sorted(p for p in folder.rglob('*') if p.is_file() and '.git' not in p.relative_to(folder).parts)
        (folder/name).write_bytes(''.join(f'{sha(p)}  {p.relative_to(folder).as_posix()}\n' for p in files).encode())
    # Populate a new index and remove old tracked paths not in the new allowlist.
    git(repo, 'read-tree', '--empty')
    files = sorted(p.relative_to(repo).as_posix() for p in repo.rglob('*') if p.is_file() and '.git' not in p.relative_to(repo).parts)
    for start in range(0, len(files), 80):
        git(repo, 'add', '--', *files[start:start+80])
    git(repo, '-c', 'commit.gpgsign=false', 'commit', '-m', 'Align v1.1.0 artifacts with the complete eight-page submission')
    commit = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'merge-base', '--is-ancestor', plan['previous_study_commit'], commit)
    git(repo, '-c', 'tag.gpgsign=false', 'tag', '-a', 'v1.1.0', '-m', 'Local submission candidate v1.1.0; publication pending')
    bundle = assets / 'DocSpectraICSC2027-1.1.0.bundle'
    git(repo, 'bundle', 'create', str(bundle), 'main', 'v1.0.0', 'v1.1.0')
    sourcezip = assets / 'DocSpectraICSC2027-1.1.0-source.zip'
    pack_zip(sourcezip, {n:repo/n for n in files})
    savedzip = assets / 'DocSpectraICSC2027-1.1.0-saved-inputs.zip'
    pack_zip(savedzip, {p.relative_to(saved).as_posix():p for p in saved.rglob('*') if p.is_file()})
    result = {'version':VERSION, 'commit':commit, 'parent':plan['previous_study_commit'],
              'allowlist_sha256':sha(allowlist), 'study_files':len(files),
              'saved_files':sum(r['package']=='saved' for r in plan['files']),
              'assets':{p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(assets.iterdir())}}
    write(out/'build-report.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allowlist', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.allowlist, args.out.resolve()), indent=2))
