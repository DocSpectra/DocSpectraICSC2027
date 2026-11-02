"""Sprint 38 inventory/receipt adapter; RA owns inference, scoring and edits."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import magnetic_refinement_37 as common

ROOT=common.ROOT; CTX=common.CTX; RA=common.RA
RUN=ROOT/'artifacts/embedded-link-refinement-34-36/s38-v1'
CFG=CTX/'configs/competitive-selection-38'
VERSION='retrieval_arena.embedded_competitive.v1'
read=common.read; write=common.write; digest=common.digest; sha=common.sha; seal=common.seal


def role_bound(settings,n,bytecap=32768):
    price=settings['pricing']
    completion=((settings['max_input_tokens']+1024)*price['input_per_mtok']*1.25
                +settings['max_output_tokens']*price['output_per_mtok'])/1e6
    count=((bytecap+1024)*price['input_per_mtok']*1.25+price['output_per_mtok'])/1e6
    return round(2*n*completion,12),round(2*n*count,12)


def offline_stage(name,inp,cfg):
    write(inp/'config.json',cfg);seal(inp)
    out=RUN/(name+'-output');out.mkdir(exist_ok=False)
    (out/'.retrieval-arena-output-owner').write_bytes(b's38-offline\n')
    cmd=['pwsh','-NoProfile','-File',str(RA/'scripts/run-verification.ps1'),
        '-Command','refine-embedded-v2','-InputRoot',str(inp),'-OutputRoot',str(out),
        '-Receipt',str(RUN/'image/verification-receipt-v1.json')]
    with (RUN/(name+'.log')).open('wb') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    return out


def prepare():
    if CFG.exists():raise ValueError('preserve-existing-s38-configuration')
    CFG.mkdir()
    old=read(CTX/'configs/magnetic-refinement-37/real-policy-v1.json')
    original=read(common.ACCESS/'access-output-v2/packets.json')
    assert len(original)==26 and sum(len(p['targets']) for p in original)==81
    base=old['roles'][0]['schema']['properties']['proposals']['items']
    schema={'type':'object','additionalProperties':False,'required':['dispositions'],'properties':{
        'dispositions':{'type':'array','minItems':1,'maxItems':5,'items':{
            'type':'object','additionalProperties':False,'required':['target_id','disposition','reason','proposal'],
            'properties':{'target_id':{'type':'string','minLength':1},'disposition':{'type':'string','enum':['supported_link','no_supported_link']},
                'reason':{'type':'string','minLength':1},'proposal':{'anyOf':[base,{'type':'null'}]}}}}}}
    write(CFG/'target-dispositions.v1.json',schema)
    write(CFG/'relationship-review.v2.json',old['roles'][1]['schema'])
    generator=(CTX/'configs/magnetic-refinement-37/generator-v1.txt').read_text(encoding='utf-8')
    generator=generator.replace('Return zero, one or two proposals; omit unsupported candidates.',
        'Consider EVERY disclosed target independently. Return exactly one disposition per disclosed target, without ranking favorites.')
    generator=generator.replace('or omit the proposal.','or return no_supported_link for that target.')
    generator+='\nReturn {"dispositions": [...]} in the supplied schema. Each entry copies target_id, supplies disposition supported_link or no_supported_link, a concise reason, and proposal (one complete source-bound proposal for supported_link, null for no_supported_link). Do not fill a quota: reject a target without defensible anchor and relationship evidence. Never omit or duplicate a target. No more than one proposal per source-target pair.\n'
    (CFG/'generator-v1.txt').write_text(generator,encoding='utf-8')
    eligibility=(CTX/'configs/magnetic-refinement-37/eligibility-v1.txt').read_text(encoding='utf-8')
    confirmation=(CTX/'configs/magnetic-refinement-37/assessment-v1.txt').read_text(encoding='utf-8')
    confirmation=confirmation.replace('For independent final assessment','For independent preselection support confirmation')
    for name,txt in [('eligibility',eligibility),('confirmation',confirmation)]:
        (CFG/(name+'-v1.txt')).write_text(txt,encoding='utf-8')
    lifecycle=copy.deepcopy(old['lifecycle_policy']);lifecycle['maximum_count_request_bytes']=32768
    lanes={};total=0;completion_total=0
    for lane in ('real','preflight'):
        roles=[]
        for previous,name,n in zip(old['roles'],('generation','eligibility','support_confirmation'),
                                   (26,81,81) if lane=='real' else (1,2,2)):
            role=copy.deepcopy(previous);role['role']=name;role['initial_items_max']=n;role['attempts_max']=2*n
            text={'generation':generator,'eligibility':eligibility,'support_confirmation':confirmation}[name]
            role['prompt_prefix']=text;role['prompt_sha256']=__import__('hashlib').sha256(text.encode()).hexdigest()
            role['schema']=schema if name=='generation' else old['roles'][1]['schema']
            role['schema_sha256']=digest(role['schema'])
            role['schema_file_sha256']=sha(CFG/('target-dispositions.v1.json' if name=='generation' else 'relationship-review.v2.json'))
            if name=='generation':role['settings']['max_output_tokens']=6000
            role['settings']['pricing']['source']='Official model overview fetched 2026-11-01 Sprint38: '+{
                'generation':'https://developers.openai.com/api/docs/models/gpt-6-luna',
                'eligibility':'https://platform.claude.com/docs/en/models/haiku-5-5/overview',
                'support_confirmation':'https://developers.openai.com/api/docs/models/gpt-6.1-sol'}[name]
            completion,count=role_bound(role['settings'],n)
            if name=='generation':count=0  # Initial generation counts have their own ledger.
            role.update(completion_reservation_max_usd=completion,count_contingency_max_usd=count)
            role['settings']['spend_cap_usd']=round(completion+count,12)
            total+=completion+count;completion_total+=completion;roles.append(role)
        policy=copy.deepcopy(old)
        policy.update(run_id='s38-v1-'+lane,roles=roles,aggregate_hard_cap_usd=30,
            maximum_group_arm_edits=1,maximum_proposals_per_packet=5,
            competitive_selection_version=VERSION,lifecycle_policy=lifecycle,seed=3801,
            final_assessment='none; Sol is a preselection support gate',
            provider_token_count_complete=False,generation_counts=[],
            allowed_source_ids=sorted(p['source_id'] for p in original) if lane=='real' else ['0'],
            allowed_parent_packet_hashes=[p['packet_sha256'] for p in original] if lane=='real' else [])
        write(CFG/(lane+'-policy-v1.json'),policy);lanes[lane]=policy
    generation_count_single=((32768+1024)*.1*1.25+.5)/1e6
    caps={lane:round(n*generation_count_single,12) for lane,n in [('real',52),('preflight',2)]}
    manifest={'schema_version':'icsc.competitive_selection_38.combined_manifest.v1','run_id':'s38-v1',
        'interface_version':old['interface_version'],'competitive_selection_version':VERSION,
        'status':'offline preparation; not authorized','aggregate_hard_cap_usd':30,
        'maximum_reserved_usd':round(total+sum(caps.values()),12),
        'maximum_completion_reservation_usd':round(completion_total,12),
        'live_seconds':7200,'concurrency':2,'lifecycle_policy':lifecycle,
        'count_attempts':{'real':52,'preflight':2},'generation_count_cap_usd':caps,
        'maximum_count_attempts_all_roles':386,'maximum_completion_attempts_all_roles':386,
        'real_target_pair_count':81,'real_packet_count':26,'publish_real_pool_before_selection':True,
        'protocol_file':'design/competitive-magnetic-selection-38-v1.md',
        'protocol_sha256':sha(CTX/'design/competitive-magnetic-selection-38-v1.md'),
        'protocol_commit':'58ae402',
        'canary':'original first two packet identities; full Haiku/Sol support path; negatives pass technical check; no replacements',
        'omission_policy':'no passage or target omission; over-cap exact request omits whole packet, never replaced',
        'support_gate':'all host-valid to Haiku; all Haiku-passing to blinded Sol; both pass >=5/all dimensions>=1/no uncertainty/contradiction; Haiku rank only',
        'numerical_policy':'unchanged v2 .25 seeds3501/3502; separate .10 after primary seal; 600 sec/100MiB per group per stage; 2CPU/2GiB/one thread',
        'count_billing':'Conservative contingency at model input tariff for 32768 serialized bytes+1024 framing tokens and one output token; not invoice or admission count. Haiku counts documented free but reserved conservatively.',
        'official_sources':[r['settings']['pricing']['source'].split(': ',1)[1] for r in lanes['real']['roles']],
        'runtime_receipts':{name:read(RUN/'image'/file) for name,file in [
            ('sdk','sdk-receipt-v1.json'),('verification','verification-receipt-v1.json')]}}
    assert manifest['maximum_reserved_usd']<=30
    write(CFG/'combined-manifest-draft-v1.json',manifest)
    inp=RUN/'interface-input';inp.mkdir();common.copy_common(inp)
    shutil.copyfile(common.ACCESS/'access-output-v2/packets.json',inp/'packets.json')
    write(inp/'study-manifest.json',lanes['real'])
    offline_stage('interface',inp,{'stage':'prepare-interface'})
    for scenario in ('workflow-discrimination','translated-tie'):
        inp=RUN/(scenario+'-input');inp.mkdir()
        shutil.copytree(CTX/'configs/magnetic-refinement-35b/workflow',inp/'workflow')
        shutil.copyfile(CTX/'configs/magnetic-refinement-35b/workflow-fixture-v1.json',inp/'fixture.json')
        write(inp/'study-manifest.json',lanes['preflight'])
        offline_stage(scenario,inp,{'stage':'prepare-authored-interface','fixture_manifest':'fixture.json','corpus':'workflow','scenario':scenario})
    live=RUN/'live-input';live.mkdir()
    for lane,prepared in [('real',RUN/'interface-output'),('preflight',RUN/'workflow-discrimination-output')]:
        dest=live/lane;dest.mkdir()
        if lane=='real':common.copy_common(dest)
        else:
            for name in ('pages.json','regions.json','original.json','frozen-occurrences.json'):
                shutil.copyfile(prepared/name,dest/name)
            shutil.copytree(prepared/'corpus',dest/'corpus')
        shutil.copyfile(prepared/'packets.json',dest/'packets.json');(dest/'token-input').mkdir()
        jobs=read(prepared/('jobs.json' if lane=='real' else 'token-input/jobs.json'))
        assert all(len(json.dumps(j['body'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())<=32768 for j in jobs)
        write(dest/'token-input/jobs.json',jobs)
        policy=lanes[lane] if lane=='real' else read(prepared/'study-manifest.json')
        policy['allowed_initial_token_request_hashes']=[j['job_id'] for j in jobs]
        write(dest/'study-manifest.json',policy)
    manifest['exact_initial_generation_request_hashes']=[j['job_id'] for j in read(live/'real/token-input/jobs.json')]
    manifest['lane_manifest_hashes']={lane:digest(read(live/lane/'study-manifest.json')) for lane in ('real','preflight')}
    write(live/'study-manifest.json',manifest);seal(live)
    write(CFG/'packet-interface-freeze-v1.json',{'parent_child':read(RUN/'interface-output/parent-child.json'),
        'packet_sha256':sha(live/'real/packets.json'),'initial_jobs_sha256':sha(live/'real/token-input/jobs.json'),
        'actual_target_pairs':81,'source_files':897,'new_paragraphs_or_targets':0})
    print(json.dumps({'maximum_reserved_usd':manifest['maximum_reserved_usd'],'provider_calls':0,'ready_for_offline_qualification':True}))


def finalize():
    live=RUN/'live-input';manifest=read(live/'study-manifest.json')
    log=(RUN/'focused-v2.log').read_text(encoding='utf-8')
    assert '\nOK ' in log or '\nOK\n' in log
    smoke=json.loads((RUN/'sdk-smoke-v2.log').read_text(encoding='utf-8').strip())
    assert smoke['clients_constructed']==smoke['provider_calls']==0 and len(smoke['native_thread_pools'])==2
    mock=read(RUN/'mock-results.json');assert all(row['verified'] for row in mock)
    manifest['runtime_receipts']={name:read(RUN/'image'/file) for name,file in [
        ('sdk','sdk-receipt-v2.json'),('verification','verification-receipt-v2.json')]}
    assert all(r['source_commit']==common.git('RetrievalArena','rev-parse','HEAD') for r in manifest['runtime_receipts'].values())
    manifest.update(status='offline qualified; one explicit author yes pending',consumer_adapter_sha256=sha(Path(__file__)),
        consumer_common_helper_sha256=sha(CTX/'scripts/magnetic_refinement_37.py'),
        offline_qualification={'focused_log_sha256':sha(RUN/'focused-v2.log'),
            'sdk_smoke_sha256':sha(RUN/'sdk-smoke-v2.log'),'mock_results_sha256':sha(RUN/'mock-results.json')})
    write(live/'study-manifest.json',manifest);seal(live)
    write(CFG/'combined-manifest-v1.json',manifest)
    write(CFG/'authorization-pending-v1.json',{'approved':False,'scope':'bounded_embedded_refinement_v3',
        'study_manifest_sha256':digest(manifest),'input_inventory_sha256':read(live/'operator-request.json')['input_inventory_sha256'],
        'aggregate_hard_cap_usd':30,'maximum_reserved_usd':manifest['maximum_reserved_usd'],
        'required_author_response':'Explicit yes to the complete revised Sprint 38 manifest; Sprint start does not authorize provider calls.'})
    print(json.dumps({'approved':False,'manifest_sha256':digest(manifest),'maximum_reserved_usd':manifest['maximum_reserved_usd']}))


def authorize(response):
    if not response.strip():raise ValueError('explicit-author-response-required')
    dest=CFG/'authorization-v1.json'
    if dest.exists():raise ValueError('preserve-existing-authorization')
    pending=read(CFG/'authorization-pending-v1.json')
    assert pending['study_manifest_sha256']==digest(read(RUN/'live-input/study-manifest.json'))
    write(dest,{**pending,'approved':True,'author_response':response,'approval_date':'2026-11-01',
        'scope_note':'Full revised Sprint38 counts/preflight/canary/all-target generation/Haiku/Sol support/pool freeze/k1 copies and comparison; preserve clock/caches/capacity.'})
    out=RUN/'live-output';out.mkdir(exist_ok=False);(out/'.retrieval-arena-output-owner').write_bytes(b's38-live\n')


def support_freeze(out):
    return {'pool_sha256':digest(read(out/'real-pool-output/common-pool.json')),
        'generation_dispositions':read(out/'real-generation-merged/summary.json'),
        'haiku_jobs':read(out/'real-pool-input/model-jobs.json'),
        'haiku_reviews':read(out/'real-eligibility-merged/summary.json'),
        'sol_jobs':[j for part in (0,1) for j in read(out/f'real-{part}-confirmation-input/jobs.json')],
        'sol_confirmations':read(out/'real-confirmation-merged/summary.json'),
        'purpose':'Complete declarations and review population, including reviews preceding a later global pool-conflict rejection; no selection outcomes.'}


def launch():
    auth=read(CFG/'authorization-v1.json')
    if auth.get('approved') is not True:raise ValueError('explicit-author-yes-required')
    cmd=[sys.executable,'-B',str(RA/'scripts/embedded-api.py'),'study',
        '--receipt',str(RUN/'image/sdk-receipt-v2.json'),'--input-root',str(RUN/'live-input'),
        '--output-root',str(RUN/'live-output'),'--authorization',str(CFG/'authorization-v1.json')]
    attempt=subprocess.run(cmd)
    out=RUN/'live-output'
    if attempt.returncode and (out/'checkpoint.json').exists() and read(out/'checkpoint.json').get('reason')=='real-pool-publication-required-before-selection':
        frozen=read(out/'real-pool-output/common-pool.json');dest=CFG/'common-pool-v1.json'
        if dest.exists() and read(dest)!=frozen:raise ValueError('published-pool-drift')
        write(dest,frozen)
        support=support_freeze(out);support_dest=CFG/'support-freeze-v1.json'
        if support_dest.exists() and read(support_dest)!=support:raise ValueError('published-support-freeze-drift')
        write(support_dest,support)
        paths=['configs/competitive-selection-38/common-pool-v1.json','configs/competitive-selection-38/support-freeze-v1.json']
        subprocess.run(['git','-C',str(CTX),'add',*paths],check=True)
        changed=subprocess.run(['git','-C',str(CTX),'diff','--cached','--quiet','--',*paths])
        if changed.returncode==1:
            subprocess.run(['git','-C',str(CTX),'commit','--only','-m',
                'Freeze Sprint 38 two-model-supported pool before magnetic scoring',
                '--',*paths],check=True)
        elif changed.returncode:raise ValueError('pool-index-check-failed')
        committed=json.loads(subprocess.check_output(['git','-C',str(CTX),'show','HEAD:configs/competitive-selection-38/common-pool-v1.json']))
        if committed!=frozen:raise ValueError('pool-not-committed-before-selection')
        committed_support=json.loads(subprocess.check_output(['git','-C',str(CTX),'show','HEAD:configs/competitive-selection-38/support-freeze-v1.json']))
        if committed_support!=support:raise ValueError('reviews-not-committed-before-selection')
        commit=common.git('context','rev-parse','HEAD')
        write(out/'real-pool-publication.json',{'pool_sha256':digest(frozen),'context_commit':commit,
            'context_path':'configs/competitive-selection-38/common-pool-v1.json','context_file_sha256':sha(dest),
            'complete_support_freeze_sha256':sha(support_dest)})
        subprocess.run(cmd,check=True)
    elif attempt.returncode:raise subprocess.CalledProcessError(attempt.returncode,cmd)


def status():
    out=RUN/'live-output'
    print(json.dumps({'authorization_present':(CFG/'authorization-v1.json').exists(),
        'live_started':(out/'study-binding.json').exists(),
        'summary':read(out/'summary.json') if (out/'summary.json').exists() else None,
        'checkpoint':read(out/'checkpoint.json') if (out/'checkpoint.json').exists() else None},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','finalize','authorize','launch','status']);p.add_argument('--author-response',default='')
    args=p.parse_args()
    if args.command=='authorize':authorize(args.author_response)
    else:globals()[args.command]()
