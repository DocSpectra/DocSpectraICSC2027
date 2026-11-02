"""Independent saved-artifact accounting and freeze chronology checks for 23b."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[3]
CTX=ROOT/'git/context'
OUT=ROOT/'artifacts/magnetic-attribution-23b-v1'


def read(p):
    return json.loads(p.read_bytes())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check():
    planpath=CTX/'evidence/magnetic-attribution-23b-plan-v1-2026-10-31.json'
    selectpath=CTX/'evidence/magnetic-attribution-23b-selection-v1-2026-10-31.json'
    evidencepath=CTX/'evidence/magnetic-attribution-23b-run-v1-2026-10-31.json'
    plan,selected,e=map(read,[planpath,selectpath,evidencepath])
    frozen='b695a95'
    freeze_time=int(subprocess.check_output(['git','-C',str(CTX),'show','-s','--format=%ct',frozen]))
    for p in (planpath,selectpath):
        assert p.read_bytes()==subprocess.check_output(['git','-C',str(CTX),'show',frozen+':'+p.relative_to(CTX).as_posix()])
    assert sha(selectpath)==plan['selected_manifest_sha256'] and sha(planpath)==e['plan_sha256']
    for name,digest in plan['source_sha256'].items():
        assert sha(ROOT/'git/RetrievalArena'/name)==digest
    assert len(selected['anchors'])==14
    candidate_count=0
    for a in selected['anchors']:
        cid=a['corpus_id']
        raw=(OUT/'selection'/(cid+'.jsonl')).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==a['selection']['ledger_sha256']
        ledger=list(map(json.loads,raw.splitlines()))
        candidate_count+=len(ledger)
        for length,frame in a['selection']['by_length'].items():
            subset=[r for r in ledger if r['length']==int(length)]
            assert len(subset)==frame['candidates']
            assert sorted(subset,key=lambda r:(r['selection_hash'],r['cycle']))[:2]==frame['selected']
            assert frame['expansions']<=250000
            assert frame['candidate_stream_sha256']==hashlib.sha256(b''.join(
                json.dumps(r,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()+b'\n' for r in subset)).hexdigest()
        run=OUT/'runs'/cid
        receipt=read(run/'operator_receipt.v1.json')
        assert datetime.fromisoformat(receipt['started_at'].replace('Z','+00:00')).timestamp()>=freeze_time
        records=list(map(json.loads,(run/'magnetic_attribution.v1.jsonl').read_bytes().splitlines()))
        selection=next(r for r in records if r['kind']=='cycle_sample')
        assert selection['spectral_inputs_read'] is False and selection['result']==a['selection']
        assert (run/'artifacts'/selection['candidate_ledger']).read_bytes()==raw
        for record in records:
            if record['kind']!='pair':
                continue
            assert record['controls']['passed']
            for block in record['q'].values():
                for side in ('a','b'):
                    value=block[side]
                    assert value['numerics']['status']=='qualified'
                    assert value['fields']['energy_sum_identity_ok']
                    assert abs(value['fields']['mass_sum']-1)<=1e-10
    pairs=read(OUT/'analysis/all-pairs.json')
    primary=[r for r in pairs if r['q']==.25]
    assert len(primary)==sum(len(a['selection']['selected']) for a in selected['anchors'])
    assert len(primary)==e['summary']['selected_pairs']
    assert sum(r['resolved'] is True for r in primary)==e['summary']['resolved_primary_pairs']
    for r in pairs:
        assert r['matched'] and r['qualified']
        assert r['resolved']==(abs(r['delta'])>r['threshold'])
        assert all(r[s]['fields']['energy_sum_identity_ok'] for s in ('a','b'))
        assert all(abs(r[s]['fields']['mass_sum']-1)<1e-10 for s in ('a','b'))
        if r['finite_change']['status']=='computed':
            fc=r['finite_change']
            assert fc['account_ok'] and fc['relaxation_nonpositive']
            assert fc['fixed_mode_edge_sum_residual']<=1e-10
            assert fc['unchanged_edges_fixed_mode_max_abs']<=1e-10
    assert e['summary']['global_controls_all_pass'] and e['summary']['q0_all_exact_zero']
    assert all(e['preserved'].values()) and not e['failures']
    for name,digest in e['derived_outputs'].items():
        assert sha(OUT/name)==digest
    result={'schema_version':'icsc.magnetic_attribution_23b_check.v1','passed':True,
        'freeze_commit':frozen,'freeze_precedes_all_new_outcomes':True,'selection_manifest_unchanged':True,
        'candidate_ledger_rows':candidate_count,'corpora':14,'primary_pairs':len(primary),
        'all_pair_q_rows':len(pairs),'run_evidence_sha256':sha(evidencepath),
        'report_sha256':sha(CTX/'reports/2026-10-31-magnetic-attribution-23b-results.md'),
        'check_script_sha256':sha(Path(__file__)),
        'paper_unchanged':all(sha(ROOT/'git'/name)==digest for name,digest in
            read(OUT/'preexisting/hashes.json').items() if name.startswith('paper/'))}
    assert result['paper_unchanged']
    target=CTX/'evidence/magnetic-attribution-23b-check-v1-2026-10-31.json'
    target.write_bytes(json.dumps(result,sort_keys=True,indent=1).encode()+b'\n')
    print(json.dumps(result,indent=1))


if __name__=='__main__':
    check()
