"""Consumer-owned fixed Docker inventory. Reusable execution belongs to RA."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import shutil

ROOT = Path(__file__).resolve().parents[3]
CTX = ROOT / 'git/context'
RA = ROOT / 'git/RetrievalArena'
RUN = ROOT / 'artifacts/embedded-link-refinement-34-36/s41-v1'
SOURCE = ROOT / 'artifacts/embedded-link-refinement-34-36/s40-v1/live-input/real'
CFG = CTX / 'configs/curated-real-link-benchmark-41'

def read(p): return json.loads(p.read_bytes())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p, x):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(json.dumps(x, indent=2, sort_keys=True, ensure_ascii=False).encode() + b'\n')

def inventory():
    assert not CFG.exists(), 'inventory already frozen'
    pages = read(SOURCE/'pages.json'); regions = read(SOURCE/'regions.json')
    by = {p['path']:p for p in pages}
    assert len(pages) == 897 and all(sha(SOURCE/'corpus'/p['path']) == p['raw_sha256'] for p in pages)
    pairs = [
        ('guides/dotnet/run-tests', 'guides/dotnet/configure-ci-cd'),
        ('guides/deno/develop', 'guides/deno/configure-ci-cd'),
        ('guides/java/develop', 'guides/java/run-tests'),
        ('guides/bun/develop', 'guides/bun/configure-ci-cd'),
        ('guides/nextjs/run-tests', 'guides/nextjs/configure-github-actions'),
        ('guides/dotnet/develop', 'guides/dotnet/run-tests'),
        ('guides/java/run-tests', 'guides/java/configure-ci-cd'),
        ('guides/php/develop', 'guides/php/run-tests'),
        ('guides/php/run-tests', 'guides/php/configure-ci-cd'),
        ('guides/golang/develop', 'guides/golang/run-tests'),
        ('guides/golang/run-tests', 'guides/golang/configure-ci-cd'),
        ('guides/cpp/develop', 'guides/cpp/configure-ci-cd'),
        ('guides/r/develop', 'guides/r/configure-ci-cd'),
        ('guides/nextjs/develop', 'guides/nextjs/run-tests'),
        ('guides/nextjs/containerize', 'guides/nextjs/develop'),
        ('guides/nextjs/deploy', 'guides/nextjs/configure-github-actions'),
        ('manuals/offload/about', 'manuals/offload/quickstart'),
        ('manuals/offload/optimize', 'manuals/offload/usage'),
        ('manuals/offload/configuration', 'manuals/offload/usage'),
        ('manuals/offload/quickstart', 'manuals/offload/configuration'),
        ('manuals/extensions/_index', 'manuals/extensions/marketplace'),
        ('manuals/extensions/extensions-sdk/design/design-guidelines', 'manuals/extensions/marketplace'),
        ('manuals/engine/swarm/swarm-tutorial/deploy-service', 'manuals/engine/swarm/swarm-tutorial/scale-service'),
        ('manuals/engine/swarm/swarm-tutorial/scale-service', 'manuals/engine/swarm/swarm-tutorial/drain-node'),
        ('manuals/engine/swarm/swarm-tutorial/inspect-service', 'manuals/engine/swarm/swarm-tutorial/rolling-update'),
    ]
    rows = []
    for i, (s, t) in enumerate(pairs):
        source, target = by['content/'+s+'.md'], by['content/'+t+'.md']
        region = next(r for r in regions if source['doc_id'] in r['nodes'])
        assert target['doc_id'] in region['nodes'] and region['assignment'] == 'development'
        rows.append(dict(audit_id=f'41-{i+1:02}', mandatory=i<5, source=source, target=target,
            region_id=region['region_id'], prior_exposure='Conservatively potentially numerically exposed development case; no fresh-outcome claim.'))
    write(CFG/'audit-inventory-v1.json', dict(schema_version='icsc.curated_audit.v1',
        design_sha256=sha(CTX/'design/curated-real-link-benchmark-41-v1.md'),
        construction='Fixed named workflow pair list using paths and historical five mandatory identities; no costs or full-page curation.',
        pairs=rows, maximum_admitted=16, source_file_count=897, provider_calls=0))
    baseline = {}
    for name in ('context', 'RetrievalArena', 'paper'):
        repo=ROOT/'git'/name
        baseline[name]={k:subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,check=False).stdout
                       for k,args in [('head',['rev-parse','HEAD']),('status',['status','--porcelain']),('tags',['show-ref','--tags'])]}
    write(RUN/'baseline.json', {'repos':baseline,'incoming_files':{
        str(p.relative_to(RUN/'incoming')):sha(p) for p in (RUN/'incoming').rglob('*') if p.is_file()}})
    print(json.dumps({'fixed_pairs':len(rows),'full_pages':len({p[k]['path'] for p in rows for k in ('source','target')}),'provider_calls':0}))

def inputs():
    dest=RUN/'prepare-input';dest.mkdir()
    for name in ('pages.json','regions.json','original.json','frozen-occurrences.json'):
        shutil.copyfile(SOURCE/name,dest/name)
    shutil.copytree(SOURCE/'corpus',dest/'corpus')
    shutil.copyfile(CFG/'audit-inventory-v1.json',dest/'audit-inventory.json')
    shutil.copyfile(CFG/'curation-decisions-v1.json',dest/'curation-decisions.json')
    write(dest/'config.json',{'stage':'curated-prepare','timestamp':'2026-11-01-curator-audit','run_id':'s41-v1'})
    import magnetic_refinement_37 as common
    common.seal(dest)
    print('prepared immutable local inputs; no scores/calls')

def stage(label, inp):
    import magnetic_refinement_37 as common
    common.seal(inp)
    out=RUN/label;out.mkdir();(out/'.retrieval-arena-output-owner').write_bytes(b's41-offline\n')
    command=['pwsh','-NoProfile','-File',str(RA/'scripts/run-verification.ps1'),'-Command','refine-embedded-v2',
        '-InputRoot',str(inp),'-OutputRoot',str(out),'-Receipt',str(RUN/'image/receipt-v1.json')]
    with (RUN/(label+'.log')).open('wb') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    print(json.dumps(read(out/'summary.json')['result']))
    return out

def prepare():
    out=stage('prepare-final',RUN/'prepare-input')
    assert read(out/'curated-freeze.json')==read(RUN/'prepare-output-v2/curated-freeze.json')
    shutil.copyfile(out/'curated-freeze.json',CFG/'curated-freeze-v1.json')
    frozen=read(out/'curated-freeze.json')
    paths=sorted({p[k]['path'] for p in read(CFG/'audit-inventory-v1.json')['pairs'] for k in ('source','target')})
    write(CFG/'reading-receipt-v1.json',{'scope':'Agent read complete frozen source files, including all listed procedural/code/list content; no live site or external dependency execution.',
        'files':[{'path':p,'sha256':sha(SOURCE/'corpus'/p),'bytes':(SOURCE/'corpus'/p).stat().st_size} for p in paths],
        'all_audit_pairs':25,'first_ingress':{'admitted':11,'technical_corrections':2,'retained_artifact':'artifacts/embedded-link-refinement-34-36/s41-v1/prepare-output'},
        'final_admitted':sum(r['status']=='admitted' for r in frozen['records']),
        'prior_exposure':'All are treated as potentially exposed development cases, no fresh/unexposed outcome claim.',
        'new_provider_calls':0,'real_magnetic_calculations':0})
    shutil.copyfile(RUN/'image/receipt-v1.json',CFG/'verification-receipt-v1.json')

def freeze_receipt():
    commit=subprocess.check_output(['git','-C',str(CTX),'rev-parse','HEAD'],text=True).strip()
    committed=subprocess.check_output(['git','-C',str(CTX),'show',commit+':configs/curated-real-link-benchmark-41/curated-freeze-v1.json'])
    assert hashlib.sha256(committed).hexdigest()==sha(CFG/'curated-freeze-v1.json')
    write(CFG/'freeze-receipt-v1.json',{'context_commit':commit,'curated_freeze_sha256':sha(CFG/'curated-freeze-v1.json'),
        'design_sha256':sha(CTX/'design/curated-real-link-benchmark-41-v1.md'),
        'audit_inventory_sha256':sha(CFG/'audit-inventory-v1.json'),'RA_source':read(CFG/'verification-receipt-v1.json')['source_commit'],
        'B':'unrun-not-authorized; any later B follows exposure to A','provider_calls':0,'real_scores_before_receipt':0})

def select():
    dest=RUN/'selection-input';shutil.copytree(RUN/'prepare-input',dest)
    shutil.copyfile(CFG/'curated-freeze-v1.json',dest/'curated-freeze.json')
    shutil.copyfile(CFG/'freeze-receipt-v1.json',dest/'freeze-receipt.json')
    write(dest/'config.json',{'stage':'curated-select','diagnostic':True})
    stage('selection-output',dest)

def verify():
    dest=RUN/'verify-input';shutil.copytree(RUN/'selection-input',dest)
    shutil.copytree(RUN/'selection-output',dest/'result')
    shutil.copyfile(RUN/'selection-output/ledger-anchors.json',dest/'ledger-anchors.json')
    write(dest/'config.json',{'stage':'curated-verify'})
    stage('verification-output',dest)

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage', choices=['inventory','inputs','prepare','freeze_receipt','select','verify'])
    args=parser.parse_args();globals()[args.stage]()
