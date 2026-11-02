"""Consumer freeze and thin immutable-operator adapter for Sprint 41c."""
import argparse
import json
import copy
import hashlib
import shutil
import subprocess
from pathlib import Path
import minimum_cost_set_41b as previous

ROOT=previous.ROOT
CTX=previous.CTX
RA=previous.RA
RUN=ROOT/'artifacts/embedded-link-refinement-34-36/s41c-v1'
CFG=CTX/'configs/blinded-support-review-41c'
read,write,sha=previous.read,previous.write,previous.sha

def baseline():
    previous.RUN=RUN
    # The same historical source/results are used and must remain immutable.
    previous.baseline()

def stage(label, inp):
    from magnetic_refinement_37 import seal
    seal(inp)
    out=RUN/label;out.mkdir();(out/'.retrieval-arena-output-owner').write_bytes(b's41c-offline\n')
    cmd=['pwsh','-NoProfile','-File',str(RA/'scripts/run-verification.ps1'),'-Command','refine-embedded-v2',
         '-InputRoot',str(inp),'-OutputRoot',str(out),'-Receipt',str(RUN/'image/verification-receipt-v3.json')]
    with (RUN/(label+'.log')).open('wb') as log:
        subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    print(json.dumps(read(out/'summary.json')['result']))

def inputs():
    dest=RUN/'prepare-input';shutil.copytree(previous.RUN/'selection-input',dest)
    write(dest/'config.json',{'stage':'blinded-support-prepare'})
    shutil.copyfile(CFG/'study-policy-v1.json',dest/'study-manifest.json')
    shutil.copyfile(CFG/'synthetic-fixtures-v1.json',dest/'synthetic-fixtures.json')
    write(dest/'protocol-binding.json',{'protocol_sha256':sha(CTX/'design/blinded-support-review-41c-v1.md')})

def prepare():stage('prepare-output-v2',RUN/'prepare-input')

def bundle():
    dest=RUN/'live-input';shutil.copytree(RUN/'prepare-input',dest)
    for name in ('jobs.json','private-bindings.json','preflight-jobs.json'):
        shutil.copyfile(RUN/'prepare-output-v2'/name,dest/name)
    print('Prepared all 26 real and four synthetic jobs; no provider clients.')

def policy():
    from magnetic_refinement_38 import role_bound
    CFG.mkdir(exist_ok=False)
    review=(CTX/'configs/corrected-competitive-selection-40/relation-use-review-v3.txt').read_text(encoding='utf-8')
    shutil.copyfile(CTX/'configs/corrected-competitive-selection-40/relation-use-review-v3.txt',CFG/'relation-use-review-v3.txt')
    schema=read(CTX/'configs/competitive-selection-38/relationship-review.v2.json')
    write(CFG/'relationship-review.v2.json',schema)
    prior=read(CTX/'configs/corrected-competitive-selection-40/real-policy-v1.json')
    roles=[]
    for role in prior['roles'][1:]:
        role=copy.deepcopy(role);settings=role['settings']
        settings.update(max_input_tokens=32768,max_output_tokens=1800)
        settings['pricing']['source']='Proposed historical ceiling only; current official identity/context/tariff verification required after explicit Sprint41c authorization.'
        completion,count=role_bound(settings,15)
        settings['spend_cap_usd']=round(completion+count,12)
        role.update(initial_items_max=15,attempts_max=30,prompt_prefix=review,prompt_sha256=hashlib.sha256(review.encode()).hexdigest(),
            schema=schema,schema_sha256=hashlib.sha256(json.dumps(schema,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest(),
            reservation_per_job_usd=round((completion+count)/15,12),completion_reservation_usd=completion,count_contingency_usd=count)
        roles.append(role)
    policy={'blinded_support_version':'retrieval_arena.embedded_blinded_support.v1',
        'interface_version':prior['interface_version'],'competitive_selection_version':prior['competitive_selection_version'],
        'constraint_version':prior['constraint_version'],'run_id':'s41c-v1','candidate_count':13,
        'aggregate_hard_cap_usd':30,'maximum_reserved_usd':round(sum(r['settings']['spend_cap_usd'] for r in roles),12),
        'roles':roles,'lifecycle_policy':prior['lifecycle_policy'],'live_seconds':7200,'concurrency':2,
        'request_timeout_seconds':120,'maximum_count_attempts':60,'maximum_completion_attempts':60,
        'maximum_technical_retries_per_job':1,'substantive_retries':0,'provider_token_count_complete':False,
        'status':'offline preparation; no outbound authorization',
        'protocol_sha256':sha(CTX/'design/blinded-support-review-41c-v1.md')}
    assert policy['maximum_reserved_usd']<=30
    write(CFG/'study-policy-v1.json',policy)
    write(CFG/'synthetic-fixtures-v1.json',[
        {'key':'positive','source':'Next, learn validation rules.\n','target':'Validation rules: run validation before publishing.\n',
         'sentence':'Next, learn validation rules.','anchor':'validation rules','expected_pass':True},
        {'key':'negative','source':'Next, learn validation rules.\n','target':'Release notes list unrelated past announcements.\n',
         'sentence':'Next, learn validation rules.','anchor':'validation rules','expected_pass':False}])
    print(json.dumps({'maximum_reserved_usd':policy['maximum_reserved_usd'],'provider_calls':0}))

def finalize():
    from magnetic_refinement_37 import seal,digest
    live=RUN/'live-input';manifest=read(live/'study-manifest.json')
    results=read(RUN/'qualified-results-v3.json')
    assert len(results)==3 and all(r['cached_restart'] and r['verification']['independent_replay'] for r in results)
    log=(RUN/'focused-v3.log').read_text(encoding='utf-8')
    assert 'Ran 755 tests' in log and 'OK (skipped=2)' in log
    inventory=read(RUN/'serialized-requests-final/request-inventory.json')
    assert len(inventory)==30 and max(r['body_bytes'] for r in inventory)==29340
    manifest.update(status='offline qualified; explicit combined authorization pending',
        consumer_adapter_sha256=sha(Path(__file__)),offline_qualifier_sha256=sha(CTX/'scripts/blinded_support_41c_qualify.py'),
        final_request_hashes=read(RUN/'serialized-requests-final/request-hashes.json'),
        request_inventory_sha256=digest(inventory),
        runtime_receipts={name:read(RUN/'image'/file) for name,file in [
            ('sdk','sdk-receipt-v3.json'),('verification','verification-receipt-v3.json')]},
        source_inventory={'frozen_candidates':13,'complete_unique_pages':23,'source_files':897,'groups':4,
            'curated_freeze_sha256':sha(live/'curated-freeze.json'),'private_bindings_sha256':sha(live/'private-bindings.json'),
            'jobs_file_sha256':sha(live/'jobs.json'),'preflight_jobs_file_sha256':sha(live/'preflight-jobs.json')},
        numerical_policy={'selector':'retrieval_arena.embedded_selection.minimum_set.v1','maximum':1,'q':.25,
            'seeds':[3501,3502],'starts':8,'rank_atol':1e-6,'group_seconds':600,'group_output_bytes':100*1024**2,
            'cpus':2,'memory_bytes':2*1024**3,'native_threads':1},
        resources={'provider_input_tokens':32768,'provider_output_tokens':1800,'maximum_serialized_request_bytes':32768,
            'maximum_observed_request_bytes':29340,'model_context_minimum_tokens':34568},
        ordering='audit order within each model; preflight positive/negative for Haiku then Sol, real all thirteen Haiku then all thirteen Sol; concurrency two inside a role',
        seeds={'semantic_tie_seed':3801,'provider_sampling_seed':None},
        official_sources={'eligibility':'https://platform.claude.com/docs/en/models/haiku-5-5/overview',
            'support_confirmation':'https://developers.openai.com/api/docs/models/gpt-6.1-sol'},
        qualification={name:sha(RUN/file) for name,file in [
            ('focused','focused-v3.log'),('exact_fake_paths','qualified-results-v3.json'),
            ('sdk_smoke','sdk-smoke-v3.log'),('final_sdk_requests','serialized-requests-final/request-inventory.json')]})
    frozen=read(live/'curated-freeze.json');source_inventory=[]
    tiers={p['proposal_id']:p for g in frozen['A']['groups'] for p in g['pool']}
    for row in frozen['records']:
        if row['status']!='admitted':continue
        p=row['proposal'];packet=frozen['packets'][row['packet_sha256']]
        source_inventory.append({'audit_id':row['audit_id'],'proposal_id':p['proposal_id'],'region_id':p['region_id'],
            'source_path':row['pair']['source']['path'],'target_path':row['pair']['target']['path'],
            'anchor':p['anchor'],'source_sentence':p['sentence'],'source_span':p['source_span'],'tie_id':tiers[p['proposal_id']]['tie_id'],
            'packet_sha256':row['packet_sha256'],'pages':[{k:page[k] for k in ('doc_id','role','sha256','passage_id','raw_span')} for page in packet['pages']]})
    write(CFG/'source-request-inventory-v1.json',source_inventory)
    manifest['source_request_inventory_sha256']=sha(CFG/'source-request-inventory-v1.json')
    write(live/'study-manifest.json',manifest);seal(live)
    write(CFG/'combined-manifest-v1.json',manifest)
    for name in ('request-inventory.json','request-hashes.json'):
        shutil.copyfile(RUN/'serialized-requests-final'/name,CFG/name.replace('.json','-v1.json'))
    shutil.copyfile(live/'private-bindings.json',CFG/'private-bindings-v1.json')
    for name in ('sdk','verification'):
        shutil.copyfile(RUN/'image'/(name+'-receipt-v3.json'),CFG/(name+'-receipt-v1.json'))
    write(CFG/'authorization-pending-v1.json',{'approved':False,'scope':'bounded_embedded_refinement_v3',
        'study_manifest_sha256':digest(manifest),'input_inventory_sha256':read(live/'operator-request.json')['input_inventory_sha256'],
        'maximum_reserved_usd':manifest['maximum_reserved_usd'],'aggregate_hard_cap_usd':30,
        'required_author_response':'Explicit yes to the full Sprint41c combined package: official verification/counts/four synthetic and all26 real reviews/retries/pool publication/selection/copies/verification.'})
    print(json.dumps({'ready_for_combined_approval':True,'manifest_sha256':digest(manifest),'maximum_reserved_usd':manifest['maximum_reserved_usd']}))

def authorize(response):
    assert response.strip(),'explicit author response required'
    assert not (CFG/'authorization-v1.json').exists(),'preserve existing authorization'
    write(CFG/'authorization-v1.json',{**read(CFG/'authorization-pending-v1.json'),'approved':True,'author_response':response})

def launch():
    from magnetic_refinement_37 import digest,seal
    live=RUN/'live-input';manifest=read(live/'study-manifest.json')
    authorization=read(CFG/'authorization-v1.json');verification=read(CFG/'model-price-verification-v1.json')
    assert authorization['approved'] is True and verification['verified'] is True
    assert verification['manifest_sha256']==digest(manifest)
    bound=RUN/'authorization-with-verification-v1.json'
    data={**authorization,'official_verification':verification}
    if bound.exists():assert read(bound)==data
    else:write(bound,data)
    out=RUN/'live-output';out.mkdir(exist_ok=True)
    command=['python','-B',str(RA/'scripts/embedded-api.py'),'study','--receipt',str(RUN/'image/sdk-receipt-v3.json'),
        '--input-root',str(live),'--output-root',str(out),'--authorization',str(bound)]
    with (RUN/'live.log').open('ab') as log:
        attempt=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
    if attempt.returncode:
        if not (out/'support-pool.json').exists() or (out/'pool-publication.json').exists():
            raise RuntimeError('Inspect retained live failure and accounting before restart')
        support=read(out/'support-pool.json');relative='configs/blinded-support-review-41c/support-pool-v1.json'
        destination=CTX/relative
        if destination.exists():assert read(destination)==support
        else:write(destination,support)
        subprocess.run(['git','-C',str(CTX),'add',relative],check=True)
        if subprocess.run(['git','-C',str(CTX),'diff','--cached','--quiet','--',relative]).returncode:
            subprocess.run(['git','-C',str(CTX),'-c','commit.gpgsign=false','commit','--only','-m','Freeze Sprint 41c complete blinded review inventory before selection','--',relative],check=True)
        assert json.loads(subprocess.check_output(['git','-C',str(CTX),'show','HEAD:'+relative]))==support
        write(out/'pool-publication.json',{'support_sha256':digest(support),'context_commit':previous.git('context','rev-parse','HEAD'),
            'context_path':relative,'context_file_sha256':sha(destination)})
        with (RUN/'live.log').open('ab') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    # Anchor outside the selection output before independent verification.
    anchors=read(out/'selection/ledger-anchors.json');write(CFG/'ledger-anchors-v1.json',anchors)
    dest=RUN/'verify-input';shutil.copytree(live,dest)
    shutil.copyfile(out/'support-pool.json',dest/'support-pool.json');shutil.copytree(out/'selection',dest/'result')
    write(dest/'ledger-anchors.json',anchors);write(dest/'config.json',{'stage':'blinded-support-verify'});seal(dest)
    stage('verification-output',dest)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['baseline','policy','inputs','prepare','bundle','finalize','authorize','launch'])
    p.add_argument('--author-response',default='');args=p.parse_args()
    if args.command=='authorize':authorize(args.author_response)
    else:globals()[args.command]()
