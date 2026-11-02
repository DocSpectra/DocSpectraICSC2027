"""Read-only projection of Sprint 41c's RA review/selection/accounting artifacts."""
from collections import Counter
import json
import blinded_support_41c as c
from magnetic_refinement_37 import digest

DIMS=('relationship_support','target_support','placement')

def accounting():
    live=c.RUN/'live-output';rows=[]
    for lane in ('preflight','real'):
        for role in ('eligibility','support_confirmation'):
            folder=live/(lane+'-'+role)
            if not folder.exists():continue
            events=[json.loads(line) for line in (folder/'cost-ledger.jsonl').read_bytes().splitlines()] if (folder/'cost-ledger.jsonl').exists() else []
            opened={};reported=0.;failed=0.;count_reservation=0.
            for event in events:
                if event['event']=='reserved':opened[event['reservation']]=event['projected_usd']
                elif event['event']=='reported':opened.pop(event['reservation']);reported+=event['cost_usd']
                elif event['event']=='failed':
                    value=opened.pop(event['reservation']);failed+=value
                    if event['error']=='count-billing-contingency-no-invoice':count_reservation+=value
            summary=c.read(folder/'summary.json') if (folder/'summary.json').exists() else {}
            counts=list(folder.glob('jobs/*/count-*.json'));attempts=list(folder.glob('jobs/*/attempt-*/attempt.json'))
            row={'lane':lane,'role':role,'count_attempts':len(counts),'completion_attempts':len(attempts),
                'reported_completion_usd':reported,'count_contingency_usd':count_reservation,
                'other_failed_projection_usd':failed-count_reservation,'open_projection_usd':sum(opened.values()),
                'spent_or_reserved_usd':reported+failed+sum(opened.values()),
                'status_counts':dict(Counter(r['status'] for r in summary.get('results',[]))),
                'halt_reason':summary.get('halt_reason'),
                'technical_count_failures':sum(c.read(p)['status']=='technical_failure' for p in counts),
                'unknown_count_attempts':sum(c.read(p)['status']=='attempted' for p in counts),
                'technical_completion_failures':sum(c.read(p)['status']=='technical_failure' for p in attempts)}
            if summary:assert abs(row['spent_or_reserved_usd']-summary['spent_or_reserved_usd'])<1e-10
            rows.append(row)
    total={key:sum(r[key] for r in rows) for key in ('count_attempts','completion_attempts','reported_completion_usd',
        'count_contingency_usd','other_failed_projection_usd','open_projection_usd','spent_or_reserved_usd',
        'technical_count_failures','unknown_count_attempts','technical_completion_failures')}
    assert total['spent_or_reserved_usd']<=30+1e-10
    return {'partitions':rows,'total':total,'billing_note':'Count contingencies are reservations, not provider invoices; completion costs are provider-reported usage at the frozen verified tariff.'}

def project():
    live=c.RUN/'live-output';support=c.read(live/'support-pool.json');groups=c.read(live/'selection/groups.json')
    frozen=c.read(c.RUN/'live-input/curated-freeze.json');original={r['proposal']['proposal_id']:r for r in frozen['records'] if r['status']=='admitted'}
    audit={pid:r['audit_id'] for pid,r in original.items()};by_audit={value:key for key,value in audit.items()}
    inventory=[]
    for row in support['inventory']:
        r=original[row['proposal_id']];out={key:row[key] for key in ('audit_id','proposal_id','status','states','evidence_sha256','jobs')}
        out.update(source_path=r['pair']['source']['path'],target_path=r['pair']['target']['path'],
            anchor=r['proposal']['anchor'],source_sentence=r['proposal']['sentence'],reviews={})
        for role in ('eligibility','support_confirmation'):
            assessment=row['assessments'].get(role);response=row['responses'][role]
            out['reviews'][role]={'status':response['status'],'passed':row['states'][role]=='pass',
                'assessment':assessment,'raw_response_sha256':response.get('raw_response_sha256'),
                'request_sha256':response.get('request_sha256'),'input_tokens':response.get('input_tokens')}
        inventory.append(out)
    dispositions=Counter(r['status'] for r in inventory)
    paired=sum(r['status']!='technical_unavailable' for r in inventory)
    group_rows=[]
    for group in groups:
        row={key:group[key] for key in ('region_id','competitive','numerically_evaluable','qualified_changed_subset',
            'decision_status','pairs','intersection','symmetric_difference','candidate_costs')}
        row['candidate_costs']={pid:{'audit_id':audit[pid],'status':cost['status'],'schedule_costs':cost.get('schedule_costs')}
            for pid,cost in group['candidate_costs'].items()}
        row.update(page_count=len(next(r for r in c.read(c.RUN/'live-input/regions.json') if r['region_id']==group['region_id'])['nodes']),
            supported_count=len(group['pool']),top_tier_count=len(group['top_tier']),
            top_tier=[audit[pid] for pid in group['top_tier']],
            semantic_scores={audit[p['proposal_id']]:p['semantic_score'] for p in group['pool']},
            selected={a:[audit[pid] for pid in group['arms'][a]['selected']] for a in ('S','Z','M')},
            historical_S=[audit[pid] for pid in group['historical_semantic_winner']],
            current_S_changed_from_historical=group['arms']['S']['selected']!=group['historical_semantic_winner'],
            minimum_set=group['arms']['M']['trace'][0]['minimum_set'] if group['arms']['M']['trace'] else None)
        if row['minimum_set']:
            row['minimum_set_audit_ids']=[audit[pid] for pid in row['minimum_set']['member_ids']]
        group_rows.append(row)
    specific={}
    for key in ('41-06','41-12'):
        pid=by_audit[key];record=next(r for r in inventory if r['proposal_id']==pid)
        group=next(g for g in groups if g['region_id']==original[pid]['proposal']['region_id'])
        specific[key]={'support_status':record['status'],'both_pass':record['status']=='both_pass',
            'highest_tier':pid in group['top_tier'],
            'fresh_Haiku_score':record['reviews']['eligibility']['assessment']['score'] if record['reviews']['eligibility']['assessment'] else None}
    specific['both_survive_same_highest_tier']=all(specific[key]['both_pass'] and specific[key]['highest_tier'] for key in ('41-06','41-12'))
    return {'schema_version':'icsc.blinded_support_review_41c.real.v1','support_sha256':digest(support),
        'pool_publication':c.read(live/'pool-publication.json'),'inventory':inventory,
        'disposition_counts':dict(dispositions),'paired_available':paired,
        'binary_gate_agreement_count':dispositions['both_pass']+dispositions['both_negative'],
        'groups':group_rows,'group_denominator':len(groups),
        'competitive':sum(g['competitive'] for g in groups),'evaluable':sum(g['numerically_evaluable'] for g in groups),
        'changed':sum(g['qualified_changed_subset'] for g in groups),'specific_41b_example':specific,
        'accounting':accounting(),'independent_verification':c.read(c.RUN/'verification-output/summary.json')['result'] if (c.RUN/'verification-output/summary.json').exists() else None,
        'interpretation':'Fresh blinded AI judgments after exposed method development; descriptive support agreement, not human validity, independent replication or utility.'}

if __name__=='__main__':
    data=project();c.write(c.RUN/'real-results-projection-v1.json',data)
    print(json.dumps({key:data[key] for key in ('disposition_counts','competitive','evaluable','changed','specific_41b_example','accounting','independent_verification')}))
