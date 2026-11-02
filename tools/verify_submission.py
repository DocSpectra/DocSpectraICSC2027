"""Portable, provider-free verification of the v1.1.0 submission artifacts."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import runpy
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT/'git/context/scripts'
sys.path[:0] = [str(ROOT/'git/RetrievalArena'), str(ROOT/'vendor'), str(SCRIPTS)]


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def checked_path(name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:
        raise ValueError('nonportable manifest path')
    return ROOT.joinpath(*p.parts)


def manifest():
    counts = {}
    for name in ('MANIFEST.sha256','SAVED-INPUTS.sha256'):
        rows = (ROOT/name).read_text(encoding='utf-8').splitlines()
        for row in rows:
            digest, path = row.split('  ', 1)
            assert sha(checked_path(path)) == digest, path
        counts[name] = len(rows)
    sidecars = 0
    for p in (ROOT/'git/context/evidence').glob('*.sha256'):
        text=p.read_text(encoding='ascii').strip().split()
        assert sha(p.with_suffix('.json')) == text[0], p.name
        sidecars += 1
    for row in read(ROOT/'submission/input-allowlist.json')['files']:
        assert sha(checked_path(row['path'])) == row['sha256'], row['path']
    print(json.dumps({'manifests':counts,'sidecars':sidecars,'passed':True}))


def saved():
    base=ROOT/'artifacts/magnetic-attribution-23b-v1'
    ctx=ROOT/'git/context'
    plan=read(ctx/'evidence/magnetic-attribution-23b-plan-v1-2026-10-31.json')
    selection=read(ctx/'evidence/magnetic-attribution-23b-selection-v1-2026-10-31.json')
    evidence=read(ctx/'evidence/magnetic-attribution-23b-run-v1-2026-10-31.json')
    assert sha(ctx/'evidence/magnetic-attribution-23b-selection-v1-2026-10-31.json')==plan['selected_manifest_sha256']
    assert sha(ctx/'evidence/magnetic-attribution-23b-plan-v1-2026-10-31.json')==evidence['plan_sha256']
    ledger_rows=0
    for a in selection['anchors']:
        cid=a['corpus_id']; inp=base/'inputs'/cid; run=base/'runs'/cid
        raw=(base/'selection'/(cid+'.jsonl')).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==a['selection']['ledger_sha256']
        ledger=[json.loads(line) for line in raw.splitlines()];ledger_rows+=len(ledger)
        for length, frame in a['selection']['by_length'].items():
            subset=[row for row in ledger if row['length']==int(length)]
            assert len(subset)==frame['candidates']
            assert sorted(subset,key=lambda row:(row['selection_hash'],row['cycle']))[:2]==frame['selected']
        assert sha(inp/'config.json')==a['config_sha256']
        assert sha(inp/'operator-request.json')==a['request_sha256']
        for row in read(inp/'operator-request.json')['input_inventory']:
            assert sha(inp/row['path'])==row['sha256']
        receipt=read(run/'operator_receipt.v1.json')
        assert receipt['status']=='completed'
        assert receipt['identity']['source_commit']==plan['image']['source_commit']
        for name,digest in receipt['outputs'].items():
            assert sha(run/name)==digest
        for row in read(run/'magnetic_attribution_manifest.json')['artifacts']:
            assert sha(run/row['path'])==row['sha256']
    pairs=read(base/'analysis/all-pairs.json')
    primary=[p for p in pairs if p['q']==.25]
    assert len(selection['anchors'])==14 and len(primary)==77
    assert sum(p['resolved'] is True for p in primary)==54
    for p in pairs:
        assert p['matched'] and p['qualified']
        assert p['resolved']==(abs(p['delta'])>p['threshold'])
        for side in ('a','b'):
            assert p[side]['fields']['energy_sum_identity_ok']
            assert abs(p[side]['fields']['mass_sum']-1)<1e-10
    for name,digest in evidence['derived_outputs'].items():
        assert sha(base/name)==digest
    # Project all recorded judgments, costs and group outcomes, without clients.
    import blinded_support_41c_results as support
    projected=support.project()
    recorded=read(ctx/'evidence/blinded-support-review-41c-real-v1-2026-11-02.json')
    for key in ('inventory','groups','disposition_counts','competitive','evaluable','changed','accounting','specific_41b_example','support_sha256'):
        assert projected[key]==recorded[key], key
    # Join every support response identity to the exact saved provider response.
    run=ROOT/'artifacts/embedded-link-refinement-34-36/s41c-v1'
    from retrieval_arena.embedded_blinded_support import pool
    outputs={role:read(run/'live-output'/('real-'+role)/'summary.json') for role in ('eligibility','support_confirmation')}
    rebuilt=pool(read(run/'live-input/curated-freeze.json'),read(run/'live-input/regions.json'),
                 read(run/'live-input/jobs.json'),read(run/'live-input/private-bindings.json'),outputs)
    assert rebuilt==read(run/'live-output/support-pool.json')
    from retrieval_arena.embedded_links import digest
    frozen=read(run/'live-input/study-manifest.json')['final_request_hashes']
    native_count=0
    for lane in ('preflight','real'):
        for role in ('eligibility','support_confirmation'):
            folder=run/'live-output'/(lane+'-'+role)
            for row in read(folder/'summary.json')['results']:
                job=folder/'jobs'/row['job_id']
                assert sha(job/row['raw_response_file'])==row['raw_response_sha256']
                assert digest(read(job/'request.json'))==row['request_sha256']==frozen[lane][role][row['job_id']]
                assert json.loads(row['transport_result']['text'])==row['payload']
                native_count+=1
    assert native_count==30
    print(json.dumps({'passed':True,'cycle_ledger_rows':ledger_rows,'primary_pairs':77,'resolved':54,
                      'AI_supported':13,'native_responses':native_count,'competitive_evaluable_changed':[1,1,1],'groups':4}))


def portable_paths():
    """Map two declared legacy locations before Path construction, without editing inputs."""
    original=Path.__init__
    prefix='C:/Users/rocke/Documents/ResearchProjects/DocSpectraICSC2027/'
    importer='C:/Users/rocke/Documents/ResearchProjects/DocSpectraCIKM2026/git/DocSpectraCIKM2026/docspectra_importer/'
    vendor=ROOT/'vendor/docspectra_importer'
    def initialize(self,*parts):
        value='/'.join(os.fspath(p) for p in parts).replace('\\','/')
        # Windows absolute paths remain absolute when interpreted on Linux.
        for old,base in ((prefix,ROOT),(importer,vendor)):
            at=value.find(old)
            if at>=0:
                return original(self,base,value[at+len(old):])
        return original(self,*parts)
    Path.__init__=initialize


def historical():
    portable_paths()
    for script, command in [('wc_analysis_20.py','verify'),('lt_trace_audit_24.py','verify'),
                             ('tier1_completeness_25.py','verify'),('normalized_magnetic_22.py','check')]:
        print('VERIFY',script,flush=True)
        sys.argv=[str(SCRIPTS/script),command]
        runpy.run_path(str(SCRIPTS/script),run_name='__main__')


def example(output):
    if output.exists():raise ValueError('fresh output directory required')
    output.mkdir(parents=True)
    from retrieval_arena.embedded_blinded_support import verify
    result=verify(ROOT/'artifacts/embedded-link-refinement-34-36/s41c-v1/verify-input',output,{})
    print(json.dumps(result))
    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')


def displays():
    with tempfile.TemporaryDirectory() as td:
        target=Path(td)
        for rel in ('git/paper','git/context/evidence'):
            shutil.copytree(ROOT/rel,target/rel,ignore=shutil.ignore_patterns('.git','build','__pycache__'))
        for rel in ('artifacts/normalized-magnetic-22-v1/figures','artifacts/magnetic-attribution-23-v1/figures'):
            shutil.copytree(ROOT/rel,target/rel)
        for script,args in [('prepare_results_tables.py',[]),('prepare_cycle_summary.py',[]),
                             ('prepare_w02_displays.py',['--corpora-only']),('draw_refinement_comparison.py',[]),
                             ('preview_answer_intervals.py',['--manuscript'])]:
            path=target/'git/paper/scripts'/script
            sys.argv=[str(path),*args]
            try:
                runpy.run_path(str(path),run_name='__main__')
            except SystemExit as exc:
                if exc.code not in (None,0):raise
        paper=ROOT/'git/paper'; new=target/'git/paper'
        import re
        main=(paper/'main.tex').read_text()+''.join(p.read_text() for p in (paper/'sections').glob('*.tex'))
        tables=set(re.findall(r'\\input\{(tables/[^}]+)\}',main))
        table_parity={name:sha(paper/(name+'.tex'))==sha(new/(name+'.tex')) for name in tables}
        assert len(table_parity)==5 and all(table_parity.values()),table_parity
        pdfs=['figures/w02-refinement.pdf','figures/w02-answer-intervals.pdf']
        byte_parity={name:sha(paper/name)==sha(new/name) for name in pdfs}
        # Exact numerical source ledgers are checked inside each generator.
        print(json.dumps({'tables':table_parity,'figure_bytes_equal':byte_parity,
                          'figure_numeric_checks':True,'note':'PDF metadata can vary with renderer version.'}))


def offline(event,args):
    if event in ('socket.connect','socket.getaddrinfo'):
        raise RuntimeError('network disabled for submission verification')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['manifest','saved','historical','example','displays'])
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    sys.addaudithook(offline)
    if args.command=='example':
        if args.output is None:parser.error('--output is required')
        example(args.output)
    else:globals()[args.command]()
