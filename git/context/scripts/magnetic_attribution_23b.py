"""Offline Sprint 23b protocol shim; sampling, matching and measurement live in RA.

Stages: qualify-inputs, qualify-run, freeze, measure, analyze (separate report script).
Never runs a new intervention spectrum before the complete plan is committed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CTX = ROOT / 'git/context'
RA = ROOT / 'git/RetrievalArena'
OUT = ROOT / 'artifacts/magnetic-attribution-23b-v1'
S22 = ROOT / 'artifacts/normalized-magnetic-22-v1'
S23 = ROOT / 'artifacts/magnetic-attribution-23-v1'
DATE = '2026-10-31'
PLAN = CTX / f'evidence/magnetic-attribution-23b-plan-v1-{DATE}.json'
SELECTED = CTX / f'evidence/magnetic-attribution-23b-selection-v1-{DATE}.json'
RECEIPT = OUT / 'image/receipt.json'
Q = [0.0, 0.1, 0.25]
POLICY = {'seed':231031, 'lengths':[3,4,5,6], 'quota':2, 'budget':250000}


def read(p):
    return json.loads(Path(p).read_bytes())


def canon(v):
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write(p, v, new=False):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    if new and p.exists():
        raise RuntimeError(f'refusing-overwrite:{p}')
    p.write_bytes(json.dumps(v, sort_keys=True, ensure_ascii=False, indent=1, allow_nan=False).encode()+b'\n')
    return sha(p)


def git(*args):
    return subprocess.check_output(['git','-C',str(RA),*args], text=True).strip()


def modules():
    sys.path.insert(0, str(RA))
    from retrieval_arena.graph import magnetic_attribution as ma, magnetic_spectrum as ms, cycle_sampling as cs
    return ma, ms, cs


def request(root, config):
    write(root/'config.json', config)
    inventory = [{'path':p.relative_to(root).as_posix(), 'sha256':sha(p), 'size_bytes':p.stat().st_size}
                 for p in sorted(root.rglob('*'), key=lambda p:p.relative_to(root).parts)
                 if p.is_file() and p.name != 'operator-request.json']
    write(root/'operator-request.json', {'schema_version':'retrieval_arena.magnetic_attribution_operator_request.v1',
        'command':'measure-magnetic-attribution', 'config_path':'config.json', 'input_inventory':inventory,
        'input_inventory_sha256':hashlib.sha256(canon(inventory)).hexdigest()})


def verified_run(name, image):
    root, run = OUT/'inputs'/name, OUT/'runs'/name
    rec = read(run/'operator_receipt.v1.json')
    assert rec['status']=='completed' and rec['identity']['source_commit']==image['source_commit']
    assert rec['identity']['image_reference']==image['image']['reference']
    assert rec['config_sha256']==sha(root/'config.json') and rec['request_sha256']==sha(root/'operator-request.json')
    for item in read(root/'operator-request.json')['input_inventory']:
        assert sha(root/item['path'])==item['sha256'], (name,item['path'])
    for path, digest in rec['outputs'].items():
        assert sha(run/path)==digest, (name,path)
    manifest = read(run/'magnetic_attribution_manifest.json')
    for row in manifest['artifacts']:
        assert sha(run/row['path'])==row['sha256'], (name,row['path'])
    return [json.loads(s) for s in (run/'magnetic_attribution.v1.jsonl').read_bytes().splitlines()], rec, manifest


def run(name, remaining_seconds=8*3600):
    image = read(RECEIPT)
    assert git('rev-parse','HEAD')==image['source_commit'] and not git('status','--porcelain')
    root, out = OUT/'inputs'/name, OUT/'runs'/name
    if (out/'operator_receipt.v1.json').exists():
        verified_run(name,image)
        print('verified reuse', name, flush=True)
        return
    out.mkdir(parents=True,exist_ok=False)
    (out/'.retrieval-arena-output-owner').write_bytes(b'retrieval_arena.output_root.v1\n')
    logs = OUT/'logs'
    logs.mkdir(exist_ok=True)
    start = time.monotonic()
    with (logs/(name+'.log')).open('wb') as log:
        result = subprocess.run(['pwsh','-NoProfile','-File',str(RA/'scripts/run-verification.ps1'),
            '-Command','measure-magnetic-attribution','-InputRoot',str(root),'-OutputRoot',str(out),
            '-Receipt',str(RECEIPT)], stdout=log, stderr=subprocess.STDOUT, timeout=remaining_seconds)
    write(out/'host-timing.json',{'wall_seconds':time.monotonic()-start, 'exit_code':result.returncode,
        'network':'none','cpus':2,'memory_gib':4,'blas_threads':1})
    if result.returncode:
        raise RuntimeError(f'operator-failed:{name}:see-log')
    rows, rec, manifest = verified_run(name,image)
    print(name, len(rows), 'jobs', manifest['measure_seconds'],'seconds', flush=True)


def qualify_inputs():
    ma, _, _ = modules()
    for name in ('anchors','small'):
        dest = OUT/'inputs'/('replay-'+name)
        if dest.exists():
            raise RuntimeError('qualification-inputs-already-exist')
        shutil.copytree(S23/'inputs'/name,dest)
        config = read(dest/'config.json')
        config['schema_version']=ma.CONFIG_SCHEMA_V2
        request(dest,config)


def qualify_run():
    image = read(RECEIPT)
    for name in ('replay-small','replay-anchors'):
        run(name)
    rows, _, manifest = verified_run('replay-anchors',image)
    old = {r['job_id']:r for r in [json.loads(s) for s in
        (S23/'runs/anchors/magnetic_attribution.v1.jsonl').read_bytes().splitlines()]}
    diffs=[]
    checks=[]
    for r in rows:
        before=old[r['job_id']]
        if r['kind']=='cycle_selection':
            assert r['result']==before['result']
        else:
            assert r['controls']==before['controls']
            for q,block in r['q'].items():
                for side in ('a','b'):
                    diffs.extend(abs(x-y) for x,y in zip(block[side]['lowest_eigenvalues'],
                        before['q'][q][side]['lowest_eigenvalues']))
                checks.append(block['a']['numerics']['status']=='qualified' and block['b']['numerics']['status']=='qualified')
    assert max(diffs)<=1e-12 and all(checks)
    fixtures,_,_=verified_run('replay-small',image)
    for row in fixtures:
        if row['kind']=='pair':
            assert row['controls']['passed']
            assert all(block[side]['numerics']['status']=='qualified'
                       for block in row['q'].values() for side in ('a','b'))
            assert all(block[side]['fields']['energy_sum_identity_ok']
                       for block in row['q'].values() for side in ('a','b'))
        elif row['kind']=='single':
            assert all(block['numerics']['status']=='qualified'
                       for view in row['views'] for block in view['q'].values())
    write(OUT/'qualification.json',{'old_selection_replay':True,'old_pair_controls_equal':True,
        'old_pair_eigenvalue_max_abs_diff':max(diffs),'paired_q_blocks':len(checks),
        'qualified_all':all(checks),'replay_anchor_seconds':manifest['measure_seconds'],
        'image_receipt_sha256':sha(RECEIPT),'cycle_forest_isolate_fixtures_qualified':True})


def freeze():
    ma, ms, cs = modules()
    image = read(RECEIPT)
    qualification=read(OUT/'qualification.json')
    assert git('rev-parse','HEAD')==image['source_commit'] and not git('status','--porcelain')
    if PLAN.exists() or SELECTED.exists():
        raise RuntimeError('already-frozen')
    manifest_path=S22/'inputs/graph-input-manifest.json'
    roster=read(manifest_path)['panels']['anchors']
    assert len(roster)==14
    old_plan=read(CTX/'evidence/magnetic-attribution-23-plan-v1-2026-10-25.json')
    old_cycles={a['corpus_id']:{tuple(v['cycle']) for v in a['variants']} for a in old_plan['anchors']}
    s22rows={r['job_id']:r for r in [json.loads(s) for s in
        (S22/'runs/anchors/graph_spectra.v1.jsonl').read_bytes().splitlines()]}
    anchors=[]
    for entry in roster:
        cid=entry['provenance']['corpus_id']
        source=S22/'inputs/anchors'/entry['bundle']
        assert sha(source)==entry['bundle_sha256']
        b=read(source)
        ms.validate_bundle(b)
        nodes,edges,view=ma._view_of(b,{'kind':'induced','node_set':'historical_lwcc'})
        assert view['view_graph_sha256']==s22rows[f'anchor/{cid}#historical_lwcc']['selection']['view_graph_sha256']
        selection,ledger=cs.sample_cycles(nodes,edges,corpus=cid,**POLICY)
        ledgerpath=OUT/'selection'/(cid+'.jsonl')
        ledgerpath.parent.mkdir(parents=True,exist_ok=True)
        ledgerpath.write_bytes(ledger)
        dest=OUT/'inputs'/cid
        dest.mkdir(parents=True,exist_ok=False)
        def bundle(role,es):
            value={'schema_version':ms.BUNDLE_SCHEMA,'graph_id':cid+'/'+role,'nodes':nodes,
                'edges':[list(e) for e in es], 'canonical_graph_sha256':ms.graph_sha256(nodes,es),
                'provenance':{'source_bundle_sha256':sha(source),'role':role}}
            write(dest/(role+'.json'),value)
        bundle('baseline',edges)
        bundle('global',sorted((t,s) for s,t in edges))
        jobs=[{'job_id':cid+'#selection','kind':'cycle_sample','bundle':'baseline.json','view':{'kind':'full'},
            'corpus':cid,**POLICY},
            {'job_id':cid+'#global','kind':'pair','bundle_a':'baseline.json','bundle_b':'global.json',
             'view':{'kind':'full'},'q':Q,'control':'global_reversal','changed_arcs':[]}]
        overlap=[]
        for i,(cycle,arcs) in enumerate(zip(selection['cycles'],selection['arcs'])):
            ma.validate_cycle(edges,cycle)
            bundle('cycle'+str(i),ma.reverse_arcs(edges,arcs))
            jobs.append({'job_id':cid+'#cycle'+str(i),'kind':'pair','bundle_a':'baseline.json',
                'bundle_b':'cycle'+str(i)+'.json','view':{'kind':'full'},'q':Q,
                'control':'matched_cycle_reversal','changed_arcs':arcs,
                'selection_ref':{'job_id':cid+'#selection','index':i}})
            overlap.append(tuple(cycle) in old_cycles.get(cid,set()))
        config={'schema_version':ma.CONFIG_SCHEMA_V2,'spectrum_config':{'solver':'dense'},
            'attribution_config':{'report_modes':3},'jobs':jobs}
        request(dest,config)
        anchors.append({'corpus_id':cid,'full_nodes':len(b['nodes']),'full_arcs':len(b['edges']),
            'view_nodes':len(nodes),'view_arcs':len(edges),'node_coverage':len(nodes)/len(b['nodes']),
            'source_bundle':str(source.relative_to(ROOT)),'source_bundle_sha256':sha(source),
            'snapshot_commit':entry['provenance']['commit'],'source_provenance':entry['provenance'],
            'view_graph_sha256':view['view_graph_sha256'],'selection':selection,
            'previously_examined':overlap,'config_sha256':sha(dest/'config.json'),
            'request_sha256':sha(dest/'operator-request.json'),'candidate_ledger':str(ledgerpath.relative_to(ROOT))})
        print(cid,len(nodes),[v['candidates'] for v in selection['by_length'].values()],
              len(selection['selected']),flush=True)
    selected_hash=write(SELECTED,{'schema_version':'icsc.magnetic_attribution_23b_selection.v1',
        'policy':POLICY,'selection_contract':cs.VERSION,'anchors':anchors},new=True)
    preserved={str(p.relative_to(ROOT)):sha(p) for p in [manifest_path,
        CTX/'evidence/normalized-magnetic-22-plan-v1-2026-10-25.json',
        CTX/'evidence/normalized-magnetic-22-v1-2026-10-25.json',
        CTX/'evidence/magnetic-attribution-23-plan-v1-2026-10-25.json',
        CTX/'evidence/magnetic-attribution-23-v1-2026-10-25.json',
        RA/'retrieval_arena/graph/magnetic_spectrum.py',
        CTX/'scripts/magnetic_attribution_23.py',CTX/'scripts/normalized_magnetic_22.py']}
    historical=read(CTX/'evidence/normalized-magnetic-22-plan-v1-2026-10-25.json')['historical_inputs_sha256']
    for path,digest in historical.items():
        assert sha(ROOT/path)==digest
        preserved[path]=digest
    for a in anchors:
        preserved[a['source_bundle']]=a['source_bundle_sha256']
    # Scale the historical whole-anchor replay by summed n^3 work. Factor 4
    # covers matching, extra modes, shared-memory overhead and slower cells.
    denominator=sum(next(a['view_nodes'] for a in anchors if a['corpus_id']==cid)**3*13 for cid in old_cycles)
    numerator=sum(a['view_nodes']**3*(5+2*len(a['selection']['selected'])) for a in anchors)
    estimate=qualification['replay_anchor_seconds']*numerator/denominator*4
    assert estimate < 8*3600, 'resource-estimate-exceeds-budget-no-outcomes-run'
    storage_estimate=sum(a['view_arcs']*250*(5+2*len(a['selection']['selected']))
                         +a['view_nodes']*200*(5+2*len(a['selection']['selected'])) for a in anchors)
    storage_estimate+=sum(p.stat().st_size for p in (OUT/'selection').glob('*.jsonl'))*2
    assert storage_estimate < 6*1024**3
    write(PLAN,{'schema_version':'icsc.magnetic_attribution_23b_plan.v1','created':DATE,
        'status':'frozen before new intervention spectra','study':'exploratory convenience snapshots; known S22/S23 outcomes',
        'image':image,'image_receipt_sha256':sha(RECEIPT),'ra_branch':git('branch','--show-current'),
        'source_sha256':{str(p.relative_to(RA)):sha(p) for p in [Path(ma.__file__),Path(ms.__file__),Path(cs.__file__)]},
        'context_source_sha256':{p.name:sha(p) for p in [Path(__file__),
            Path(__file__).with_name('magnetic_attribution_23b_report.py')]},
        'selected_manifest_sha256':selected_hash,'selection_policy':POLICY,'q':Q,'primary_q':0.25,
        'qualification':qualification,'focused_log_sha256':sha(OUT/'image/focused.log'),
        'spectrum_config':{'solver':'dense'},'attribution_config':{'report_modes':3},
        'resolution_rule':ma.RESOLUTION_POLICY,'cluster_rule':ma.CLUSTER_POLICY,'energy_rule':ma.ENERGY_POLICY,
        'resources':{'container_cpus':2,'container_memory_gib':4,'blas_threads':1,
            'aggregate_production_seconds_max':28800,'output_target_gib':6,
            'estimated_seconds_with_4x_margin':estimate,'quota_reduced':False,
            'estimated_output_bytes':storage_estimate,
            'storage_estimate_basis':'250 bytes per arc and 200 per node per distinct solve, plus two candidate ledgers; no dense eigenbases'},
        'reporting':{'all_fourteen':True,'quantiles':'linear interpolation (numpy default)',
            'independence_unit':'corpus; no pooled significance tests','aggregate':'eligible corpora equally weighted',
            'case_card':'first by length then selection hash; no effect selection',
            'lambda2':'descriptive, with ordering/multiplicity caveat',
            'adoption':'proposal only; no manuscript or release changes'},
        'preserved_sha256':preserved,'calls':{'model':0,'retrieval':0,'outbound':0}},new=True)
    print('frozen',sha(PLAN),flush=True)


def measure():
    p=read(PLAN)
    assert sha(SELECTED)==p['selected_manifest_sha256']
    for path in (PLAN,SELECTED):
        committed=subprocess.check_output(['git','-C',str(CTX),'show','HEAD:'+path.relative_to(CTX).as_posix()])
        assert committed==path.read_bytes(), 'freeze-must-be-committed'
    for a in read(SELECTED)['anchors']:
        assert sha(OUT/'inputs'/a['corpus_id']/'config.json')==a['config_sha256']
        assert sha(OUT/'inputs'/a['corpus_id']/'operator-request.json')==a['request_sha256']
        elapsed=sum(read(f)['wall_seconds'] for f in (OUT/'runs').glob('*/host-timing.json'))
        if elapsed>=p['resources']['aggregate_production_seconds_max']:
            raise RuntimeError('aggregate-production-budget-reached')
        run(a['corpus_id'],p['resources']['aggregate_production_seconds_max']-elapsed)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['qualify-inputs','qualify-run','freeze','measure'])
    args=parser.parse_args()
    {'qualify-inputs':qualify_inputs,'qualify-run':qualify_run,'freeze':freeze,'measure':measure}[args.stage]()
