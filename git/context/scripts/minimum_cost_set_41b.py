"""Thin consumer adapter for the frozen, provider-free minimum-set comparison."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
CTX = ROOT/'git/context'
RA = ROOT/'git/RetrievalArena'
OLD = ROOT/'artifacts/embedded-link-refinement-34-36/s41-v1'
RUN = ROOT/'artifacts/embedded-link-refinement-34-36/s41b-v1'
CFG = CTX/'configs/minimum-cost-set-selection-41b'

def read(p): return json.loads(p.read_bytes())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_bytes(json.dumps(x,indent=2,sort_keys=True,ensure_ascii=False).encode()+b'\n')
def git(repo,*args): return subprocess.check_output(['git','-C',str(ROOT/'git'/repo),*args]).decode().strip()

def baseline():
    RUN.mkdir(exist_ok=True)
    assert not (RUN/'baseline.json').exists(), 'baseline already frozen'
    repos={}
    for name in ('context','RetrievalArena','paper'):
        paths=subprocess.check_output(['git','-C',str(ROOT/'git'/name),'ls-files','--modified','--others','--exclude-standard','-z']).decode().split('\0')
        dirty={}
        for path in filter(None,paths):
            source=ROOT/'git'/name/path
            if source.is_file():
                dirty[path]=sha(source);dest=RUN/'incoming'/name/path;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
        repos[name]={'head':git(name,'rev-parse','HEAD'),'status':git(name,'status','--porcelain'),
            'tags':git(name,'for-each-ref','--format=%(refname) %(objectname) %(*objectname)','refs/tags'),'dirty':dirty}
    oldfiles=[p for folder in ('selection-input','selection-output') for p in (OLD/folder).rglob('*') if p.is_file()]
    write(RUN/'baseline.json',{'repos':repos,'historical_files':{p.relative_to(ROOT).as_posix():sha(p) for p in oldfiles},
        'protocol_sha256':sha(CTX/'design/minimum-cost-set-selection-41b-v1.md')})
    print('Baseline saved; historical inputs/results and incoming edits hashed.')

def inputs():
    dest=RUN/'selection-input';shutil.copytree(OLD/'selection-input',dest)
    shutil.copytree(OLD/'selection-output',dest/'historical')
    write(dest/'config.json',{'stage':'minimum-set-select'})
    write(dest/'study-binding.json',{'protocol_sha256':sha(CTX/'design/minimum-cost-set-selection-41b-v1.md'),
        'historical_receipt':read(CTX/'configs/curated-real-link-benchmark-41/verification-receipt-v1.json'),
        'historical_files':{p.relative_to(OLD/'selection-output').as_posix():sha(p) for p in (OLD/'selection-output').rglob('*') if p.is_file()}})
    print('Prepared exact historical input/result copies.')

def stage(label,inp,receipt='receipt-v2.json'):
    from magnetic_refinement_37 import seal
    seal(inp);out=RUN/label;out.mkdir();(out/'.retrieval-arena-output-owner').write_bytes(b's41b-v1\n')
    cmd=['pwsh','-NoProfile','-File',str(RA/'scripts/run-verification.ps1'),'-Command','refine-embedded-v2',
        '-InputRoot',str(inp),'-OutputRoot',str(out),'-Receipt',str(RUN/'image'/receipt)]
    with (RUN/(label+'.log')).open('wb') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    print(json.dumps(read(out/'summary.json')['result']))

def select(): stage('selection-output',RUN/'selection-input')
def verify():
    dest=RUN/'verify-input';shutil.copytree(RUN/'selection-input',dest)
    shutil.copytree(RUN/'selection-output',dest/'result')
    shutil.copyfile(RUN/'selection-output/ledger-anchors.json',dest/'ledger-anchors.json')
    write(dest/'config.json',{'stage':'minimum-set-verify'})
    stage('verification-output',dest)

def reverify():
    stage('verification-output-v2',RUN/'verify-input','receipt-v2.json')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['baseline','inputs','select','verify','reverify'])
    globals()[parser.parse_args().stage]()
