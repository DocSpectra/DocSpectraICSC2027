"""Read-only result projection; scientific interpretation is in the dated report."""
import json
import re
import minimum_cost_set_41b as x


def main():
    root=x.RUN/'selection-output';groups=x.read(root/'groups.json')
    frozen=x.read(x.CTX/'configs/curated-real-link-benchmark-41/curated-freeze-v1.json')
    by={r['proposal']['proposal_id']:r for r in frozen['records'] if r['status']=='admitted'}
    names=lambda ids:[by[p]['audit_id'] for p in ids]
    candidates=[];rows=[]
    for group in groups:
        step=group['arms']['M']['trace'][0] if group['arms']['M']['trace'] else None
        detail=step['minimum_set'] if step else {}
        comp=group['comparison']
        rows.append({'region_id':group['region_id'],'nodes':group['nodes'],'top_tier':len(group['top_tier']),
            'finite_estimates':sum(r['status']=='finite_estimate' for r in group['candidate_costs'].values()),
            'status':step['status'] if step else 'empty_pool','minimum_set':detail,
            'member_audit_ids':names(detail.get('member_ids',[])),
            'selected_audit_ids':{a:names(comp[a]) for a in ('S','Z','M_v2','M_set')},
            'pair_comparison':{a:[ [by[p]['proposal']['source_id'],by[p]['proposal']['target_id']] for p in comp[a]]
                for a in ('intersection','symmetric_difference','removed','added')},
            'removed_audit_ids':names(comp['removed']),'added_audit_ids':names(comp['added']),
            'outcome':group['outcome']})
        for pid,cost in group['candidate_costs'].items():
            r=by[pid];p=r['proposal']
            candidates.append({'audit_id':r['audit_id'],'proposal_id':pid,'region_id':group['region_id'],
                'source':r['pair']['source']['path'],'target':r['pair']['target']['path'],
                'anchor':p['anchor'],'source_span':p['source_span'],'semantic_score':r['curator']['semantic_score'],
                'status':cost['status'],'raw_schedule_costs':cost.get('schedule_costs'),
                'normalized_schedule_costs':[s['cost_per_frozen_weight'] for s in cost.get('schedules',[])],
                'selected_by':[a for a in ('S','Z','M_v2','M_set') if pid in comp[a]]})
    structure=x.read(root/'changed-block-structures.json')
    classes=[dict(k,member_audit_ids=names(k['members']),representative_audit_id=names([k['representative']])[0]) for k in structure['classes']]
    compact={'schema_version':'icsc.minimum_cost_set.41b.structure.v1','q':structure['q'],
        'source_export_sha256':x.sha(root/'changed-block-structures.json'),'classes':classes,
        'candidates':[{k:v for k,v in row.items() if k!='schedules' and (len(row.get('mapping_to_representative',{}))<=20 or k not in ('mapping_to_representative','before_arcs','after_arcs'))}|{'audit_id':names([row['proposal_id']])[0]}
            for row in structure['candidates']],
        'representative_edge_tables':{names([k['representative']])[0]:row['schedules'][0]['edge_tables'] for k in classes
            for row in structure['candidates'] if row['proposal_id']==k['representative'] and len(row.get('mapping_to_representative',{}))<=20},
        'bulk_record_note':'Large singleton block edge tables/maps and every phase/start remain in the hash-bound ignored source export.'}
    x.write(x.CTX/'evidence/minimum-cost-set-structures-41b-v1-2026-11-01.json',compact)
    verification=x.read(x.RUN/'verification-output-v2/verification.json')
    manifest=x.read(root/'manifest.json')
    for item in manifest['outputs']:
        p=root/item['path'];snap=x.RUN/'verify-input/result'/item['path']
        assert x.sha(p)==item['sha256'] and p.stat().st_size==item['size_bytes']
        assert x.sha(snap)==item['sha256'] and snap.stat().st_size==item['size_bytes']
    verification['published_result_payload_matches_verification_snapshot']=True
    inspection=x.read(x.RUN/'card-inspection.json')
    baseline=x.read(x.RUN/'baseline.json')
    testlog=(x.RUN/'focused-v2.log').read_text()
    passed=re.search(r'^OK \(skipped=(\d+)\)',testlog,re.M)
    assert passed, 'official final suite did not pass'
    for path,h in baseline['historical_files'].items():assert x.sha(x.ROOT/path)==h,path
    paper=baseline['repos']['paper']
    assert x.git('paper','rev-parse','HEAD')==paper['head']
    assert all(x.sha(x.ROOT/'git/paper'/p)==h for p,h in paper['dirty'].items())
    for repo in ('context','RetrievalArena','paper'):
        assert x.git(repo,'for-each-ref','--format=%(refname) %(objectname) %(*objectname)','refs/tags')==baseline['repos'][repo]['tags']
    inserts=x.read(x.RUN/'tracker-insertions.json') if (x.RUN/'tracker-insertions.json').exists() else {}
    preserved=[]
    for name,h in baseline['repos']['context']['dirty'].items():
        if name in ('scripts/minimum_cost_set_41b.py','design/minimum-cost-set-selection-41b-v1.md'):continue
        raw=(x.CTX/name).read_bytes()
        if name in inserts:raw=raw.replace(inserts[name]['addition'].encode(),b'',1)
        assert x.hashlib.sha256(raw).hexdigest()==h,name
        preserved.append(name)
    evidence={'schema_version':'icsc.minimum_cost_set.41b.results.v1','protocol':'design/minimum-cost-set-selection-41b-v1.md',
        'provenance_receipt':x.read(x.CFG/'score-and-selection-binding-v1.json'),
        'groups':rows,'candidates':sorted(candidates,key=lambda r:r['audit_id']),
        'structural_classes':classes,'structural_export_sha256':x.sha(root/'changed-block-structures.json'),
        'old_competitive_evaluable_changed':[1,1,0],
        'new_competitive_evaluable_changed':[sum(g['competitive'] for g in groups),sum(g['numerically_evaluable'] for g in groups),sum(g['outcome']=='qualified_changed_subset' for g in groups)],
        'validation':verification,'card_inspection':inspection,'historical_files_verified':len(baseline['historical_files']),
        'ledger_anchors':x.read(root/'ledger-anchors.json'),
        'official_tests':{'run':int(re.search(r'^Ran (\d+) tests',testlog,re.M)[1]),'optional_skips':int(passed[1]),'failures':0},
        'verifier_receipt':x.read(x.RUN/'image/receipt-v2.json'),
        'initial_verification':{'status':'failed','reason':'Noncanonical cycle traversal flipped signed flux during independent structural replay; original receipt validated, then traversal canonicalized. No score/selection change.','log_sha256':x.sha(x.RUN/'verification-output.log')},
        'historical_and_paper_and_tags_preserved':True,
        'preserved_incoming_context_files':preserved,
        'accounting':{'provider_calls':0,'outbound_calls':0,'spend_usd':0,'new_semantic_labels':0},
        'B':'unrun-not-authorized','interpretation':'Outcome-informed method development under frozen curator labels; no independent confirmation or utility.',
        'hashes':{str(p.relative_to(x.ROOT)).replace('\\','/'):x.sha(p) for p in [root/'primary-outcomes-sealed.json',root/'manifest.json',x.RUN/'verification-output-v2/manifest.json',x.RUN/'focused-v1.log',x.RUN/'focused-v2.log']}}
    x.write(x.CTX/'evidence/minimum-cost-set-selection-41b-v1-2026-11-01.json',evidence)
    lines=['| Group | Pages | Top tier / qualified | S = Z | M-v2 | M-set | Set size | Spread | Outsider gap | Status |',
        '|---|---:|---:|---|---|---|---:|---:|---:|---|']
    for row in rows:
        nums=lambda v:'—' if v is None else f'{v:.12g}'
        show=lambda a:','.join(row['selected_audit_ids'][a]) or 'empty'
        detail=row['minimum_set']
        lines.append(f"| {row['region_id'][-8:]} | {row['nodes']} | {row['top_tier']} / {row['finite_estimates']} | {show('S')} | {show('M_v2')} | {show('M_set')} | {len(detail.get('member_ids',[]))} | {nums(detail.get('within_set_spread'))} | {nums(detail.get('outsider_gap'))} | {row['status']} |")
    lines+=['','| Candidate | Source → target | Raw schedules | Status | Selected by |','|---|---|---|---|---|']
    for row in evidence['candidates']:
        costs=', '.join(f'{v:.15g}' for v in row['raw_schedule_costs'] or [])
        lines.append(f"| {row['audit_id']} | {row['source']} → {row['target']} | {costs} | {row['status']} | {', '.join(row['selected_by']) or 'none'} |")
    (x.RUN/'comparison-table.md').write_bytes(('\n'.join(lines)+'\n').encode())
    print(json.dumps({'groups':rows,'classes':classes,'validation':verification},indent=2))


if __name__=='__main__':main()
