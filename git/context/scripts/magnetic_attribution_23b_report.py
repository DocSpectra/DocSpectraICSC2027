"""Fixed descriptive reporting for the frozen fourteen-corpus Sprint 23b study.

Run in the pinned image with the workspace mounted at /study. Writes evidence
and report to /ctx, generated tables/cards/figures to /out. No experiment selection.
"""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[3]
OUT=Path(sys.argv[2]) if len(sys.argv)>2 else ROOT/'artifacts/magnetic-attribution-23b-v1'
CTX=Path(sys.argv[3]) if len(sys.argv)>3 else ROOT/'git/context'
DATE='2026-10-31'


def read(p):
    return json.loads(Path(p).read_bytes())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_bytes(json.dumps(v,sort_keys=True,ensure_ascii=False,indent=1,allow_nan=False).encode()+b'\n')


def distribution(values):
    v=[float(x) for x in values if x is not None]
    if not v:
        return {'n':0,'min':None,'q1':None,'median':None,'q3':None,'max':None}
    result=dict(zip(('min','q1','median','q3','max'),map(float,np.quantile(v,[0,.25,.5,.75,1]))))
    return dict(n=len(v),**result)


def fmt(x):
    return 'NA' if x is None else f'{x:.3g}'


def analyze():
    sys.path.insert(0,str(ROOT/'git/RetrievalArena'))
    from retrieval_arena.graph import magnetic_spectrum as ms
    planpath=CTX/f'evidence/magnetic-attribution-23b-plan-v1-{DATE}.json'
    plan=read(planpath)
    selectionpath=CTX/f'evidence/magnetic-attribution-23b-selection-v1-{DATE}.json'
    assert sha(selectionpath)==plan['selected_manifest_sha256']
    anchors=read(selectionpath)['anchors']
    allpairs, corpora, cards, runs=[],[],[],{}
    baseline_diffs=[]
    s22={r['job_id']:r for r in map(json.loads,(ROOT/'artifacts/normalized-magnetic-22-v1/runs/anchors/graph_spectra.v1.jsonl').read_bytes().splitlines())}
    for a in anchors:
        cid=a['corpus_id']
        run=OUT/'runs'/cid
        rec=read(run/'operator_receipt.v1.json')
        assert rec['status']=='completed' and rec['identity']['source_commit']==plan['image']['source_commit']
        assert rec['identity']['image_reference']==plan['image']['image']['reference']
        assert rec['config_sha256']==a['config_sha256'] and rec['request_sha256']==a['request_sha256']
        inputroot=OUT/'inputs'/cid
        assert sha(inputroot/'config.json')==a['config_sha256'] and sha(inputroot/'operator-request.json')==a['request_sha256']
        for item in read(inputroot/'operator-request.json')['input_inventory']:
            assert sha(inputroot/item['path'])==item['sha256']
        for name,digest in rec['outputs'].items():
            assert sha(run/name)==digest
        manifest=read(run/'magnetic_attribution_manifest.json')
        for art in manifest['artifacts']:
            assert sha(run/art['path'])==art['sha256']
        records={r['job_id']:r for r in map(json.loads,(run/'magnetic_attribution.v1.jsonl').read_bytes().splitlines())}
        bundle=read(inputroot/'baseline.json')
        # The solver qualifies the entire dense basis with a Frobenius residual.
        # Its absolute value bounds each individual low-mode residual. The
        # matrix norm is invariant to q and these fixed-W reversals.
        matrix_norm=max(1.0,float(np.linalg.norm(ms.normalized_magnetic_matrix(
            bundle['nodes'],[tuple(v) for v in bundle['edges']],0.0))))
        assert records[cid+'#selection']['result']==a['selection']
        glob=records[cid+'#global']
        for q in ('0.00','0.10','0.25'):
            baseline_diffs.extend(abs(x-y) for x,y in zip(glob['q'][q]['a']['lowest_eigenvalues'],
                s22['anchor/'+cid+'#historical_lwcc']['normalized'][q]['eigenvalues'][:3]))
        pairs=[]
        for i,candidate in enumerate(a['selection']['selected']):
            row=records[cid+'#cycle'+str(i)]
            for q,b in row['q'].items():
                af=b['a'].get('fields',{})
                cc=af.get('changed_cycle',{})
                qualified=all(b[s]['numerics']['status']=='qualified' for s in ('a','b'))
                qualified=qualified and all(b[s]['cluster']['boundary_qualified'] for s in ('a','b'))
                p={'corpus_id':cid,'index':i,'job_id':row['job_id'],'q':float(q),
                    'length':candidate['length'],'cycle':candidate['cycle'],'selection_hash':candidate['selection_hash'],
                    'previously_examined':a['previously_examined'][i],
                    'matched':row['controls']['passed'],'qualified':qualified,
                    'control_checks':row['controls']['checks'],
                    'degree_checks':row['controls']['degree_checks'],
                    'trophic_abs_diff':row['controls']['trophic_incoherence']['abs_diff'],
                    'delta':b['delta_lambda1']['raw'],'threshold':b['delta_lambda1']['tolerance'],
                    'resolved':b['delta_lambda1']['resolved'] if row['controls']['passed'] and qualified else None,
                    'relative':b['delta_lambda1']['relative_to_baseline'], 'delta_lambda2':b['delta_lambda2'],
                    'a':b['a'],'b':b['b'],'finite_change':b['finite_change'],
                    'baseline_cycle_mass':cc.get('mass_on_cycle_nodes'),
                    'baseline_cycle_energy':cc.get('energy_on_changed_edges'),
                    'baseline_cycle_energy_fraction':cc.get('energy_fraction_on_changed_edges'),
                    'cycle_weight_share':cc.get('w_share_of_changed_edges'),
                    'cycle_volume_share':cc.get('degree_volume_share'),
                    'q0_reused':b['variant_solve_reused_baseline_after_exact_q0_equality']}
                p['lowest_three_residual_bounds']={side:[b[side]['numerics']['residual_relative']*matrix_norm]*
                    len(b[side]['lowest_eigenvalues']) for side in ('a','b')}
                allpairs.append(p)
                pairs.append(p)
        perq={}
        for q in (.1,.25):
            group=[r for r in pairs if r['q']==q]
            eligible=[r for r in group if r['matched'] and r['qualified']]
            base=glob['q'][f'{q:.2f}']['a']
            perq[str(q)]={'selected':len(group),'matched':sum(r['matched'] for r in group),
                'qualified':len(eligible),'resolved':sum(r['resolved'] is True for r in eligible),
                'signed_delta':distribution([r['delta'] for r in eligible]),
                'absolute_delta':distribution([abs(r['delta']) for r in eligible]),
                'relative_delta':distribution([r['relative'] for r in eligible]),
                'absolute_relative_delta':distribution([abs(r['relative']) if r['relative'] is not None else None for r in eligible]),
                'baseline_lambda1':base['lambda1'],'baseline_effective_support':base['fields']['effective_support'],
                'baseline_effective_support_fraction':base['fields']['effective_support_fraction'],
                'baseline_cluster':base['cluster'], 'baseline_numerics':base['numerics']}
        globchecks={'structural_passed':glob['controls']['passed'],
            'all_q_qualified':all(b[s]['numerics']['status']=='qualified' for b in glob['q'].values() for s in ('a','b')),
            'max_abs_delta':max(abs(b['delta_lambda1']['raw']) for b in glob['q'].values()),
            'all_q_unresolved':all(b['delta_lambda1']['resolved'] is False for b in glob['q'].values())}
        corpora.append({**{k:a[k] for k in ('corpus_id','full_nodes','full_arcs','view_nodes','view_arcs','node_coverage')},
            'search':a['selection']['by_length'],'per_q':perq,'global_reversal':globchecks})
        if pairs:
            cards.append({'corpus_id':cid,'selection':'first by frozen length then selection hash',
                'cycle':a['selection']['cycles'][0],'arcs':a['selection']['arcs'][0],
                'q_results':[r for r in pairs if r['index']==0],
                'source_page_reading':'not performed; topology/attribution only'})
        else:
            cards.append({'corpus_id':cid,'case':'no selected cycle','search':a['selection']['by_length']})
        runs[cid]={'operator_receipt_sha256':sha(run/'operator_receipt.v1.json'),
            'outputs':rec['outputs'],'measure_seconds':manifest['measure_seconds'],
            'artifact_inventory_sha256':manifest['artifact_inventory_sha256'],'artifact_count':len(manifest['artifacts']),
            'runtime':rec['runtime'],'host_timing':read(run/'host-timing.json')}
    primary=[r for r in allpairs if r['q']==.25]
    secondary=[r for r in allpairs if r['q']==.1]
    paired_sign=[(r,next(s for s in secondary if s['job_id']==r['job_id'])) for r in primary]
    eligible=[c for c in corpora if c['per_q']['0.25']['qualified']]
    equal_corpus={str(q):{'eligible_corpora':sum(c['per_q'][str(q)]['qualified']>0 for c in corpora),
        'mean_resolved_fraction':float(np.mean([c['per_q'][str(q)]['resolved']/c['per_q'][str(q)]['qualified']
            for c in corpora if c['per_q'][str(q)]['qualified']])),
        'mean_corpus_median_abs_delta':float(np.mean([c['per_q'][str(q)]['absolute_delta']['median']
            for c in corpora if c['per_q'][str(q)]['qualified']]))} for q in (.1,.25)}
    preservation={k:sha(ROOT/k.replace('\\','/'))==v for k,v in plan['preserved_sha256'].items()}
    assert all(preservation.values()) and max(baseline_diffs)<=1e-12
    evidence={'schema_version':'icsc.magnetic_attribution_23b_evidence.v1','created':DATE,
        'plan_sha256':sha(planpath),'selection_sha256':sha(selectionpath),'ra_commit':plan['image']['source_commit'],
        'image':plan['image']['image'],'source_sha256':plan['source_sha256'],'model_calls':0,'outbound_calls':0,
        'reporting_source_sha256':sha(Path(__file__)),
        'reporting_correction':'Normalize Windows path separators for Linux verification; expose conservative low-mode residual bounds from the recorded full-basis Frobenius residual; hash derived outputs; repair crowded plot ticks and add result interpretation. Selection, spectra and thresholds unchanged.',
        'runs':runs,'corpora':corpora,'equal_corpus_summary':equal_corpus,
        'summary':{'corpora':14,'corpora_with_selected_cycles':sum(bool(c['per_q']['0.25']['selected']) for c in corpora),
            'selected_pairs':len(primary),'matched_pairs':sum(r['matched'] for r in primary),
            'qualified_primary_pairs':sum(r['matched'] and r['qualified'] for r in primary),
            'resolved_primary_pairs':sum(r['resolved'] is True for r in primary),
            'resolved_primary_corpora':sum(c['per_q']['0.25']['resolved']>0 for c in corpora),
            'q0_all_exact_zero':all(r['delta']==0 and r['q0_reused'] for r in allpairs if r['q']==0),
            'global_controls_all_pass':all(all(c['global_reversal'][k] for k in
                ('structural_passed','all_q_qualified','all_q_unresolved')) for c in corpora),
            'q_sign_disagreements_both_resolved':sum(a['resolved'] is True and b['resolved'] is True and a['delta']*b['delta']<0 for a,b in paired_sign),
            'both_q_resolved_pairs':sum(a['resolved'] is True and b['resolved'] is True for a,b in paired_sign),
            'worst_ground_residual':max(r[s]['ground_residual_abs'] for r in allpairs for s in ('a','b')),
            'worst_low_mode_absolute_residual_bound':max(v for r in allpairs for b in r['lowest_three_residual_bounds'].values() for v in b),
            'energy_identity_failures':sum(r[s].get('fields',{}).get('energy_sum_identity_ok') is False for r in allpairs for s in ('a','b')),
            'mass_sum_max_abs_error':max(abs(r[s]['fields']['mass_sum']-1) for r in allpairs for s in ('a','b')),
            'finite_change_computed':sum(r['finite_change']['status']=='computed' for r in allpairs),
            'finite_change_unavailable':sum(r['finite_change']['status']!='computed' for r in allpairs),
            'finite_change_failures':sum(r['finite_change'].get('account_ok') is False or r['finite_change'].get('relaxation_nonpositive') is False for r in allpairs),
            'baseline_s22_max_abs_diff':max(baseline_diffs),
            'previously_examined_selected':sum(r['previously_examined'] for r in primary),
            'newly_examined_selected':sum(not r['previously_examined'] for r in primary),
            'production_measure_seconds':sum(r['measure_seconds'] for r in runs.values()),
            'generated_bytes':sum(p.stat().st_size for p in OUT.rglob('*') if p.is_file())},
        'overlap_companion':{name:{'pairs':len(g),'resolved':sum(r['resolved'] is True for r in g),
            'absolute_delta':distribution([abs(r['delta']) for r in g if r['matched'] and r['qualified']])}
            for name,g in [('previously_examined',[r for r in primary if r['previously_examined']]),
                           ('newly_examined',[r for r in primary if not r['previously_examined']])]},
        'preserved':preservation,'failures':[{'job_id':r['job_id'],'q':r['q'],
            'matched':r['matched'],'qualified':r['qualified']} for r in allpairs if not r['matched'] or not r['qualified']],
        'all_pairs_file':'artifacts/magnetic-attribution-23b-v1/analysis/all-pairs.json',
        'interpretation':'exploratory sensitivity beyond the exact controls, not practical utility or representative coverage'}
    write(OUT/'analysis/all-pairs.json',allpairs)
    write(OUT/'analysis/case-cards.json',cards)
    plots(allpairs)
    evidence['derived_outputs']={p.relative_to(OUT).as_posix():sha(p) for p in
        list((OUT/'analysis').glob('*.json'))+list((OUT/'figures').glob('*')) if p.is_file()}
    write(CTX/f'evidence/magnetic-attribution-23b-run-v1-{DATE}.json',evidence)
    report(evidence,primary,cards)
    print(json.dumps(evidence['summary'],indent=1))


def plots(pairs):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    primary=[r for r in pairs if r['q']==.25 and r['matched'] and r['qualified']]
    directory=OUT/'figures'
    directory.mkdir(exist_ok=True)
    fields=['corpus_id','job_id','length','delta','threshold','resolved','baseline_cycle_mass',
        'baseline_cycle_energy','baseline_cycle_energy_fraction','cycle_volume_share','cycle_weight_share']
    with (directory/'all-primary-responses.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        w.writerows({k:r[k] for k in fields} for r in primary)
    fig,axes=plt.subplots(1,2,figsize=(10,4),sharey=True)
    corpora=sorted({r['corpus_id'] for r in primary})
    palette=plt.get_cmap('tab20')
    for i,cid in enumerate(corpora):
        group=[r for r in primary if r['corpus_id']==cid]
        for ax,key in zip(axes,['baseline_cycle_mass','baseline_cycle_energy']):
            for resolved,marker in ((True,'o'),(False,'x')):
                g=[r for r in group if r['resolved']==resolved and r[key] is not None]
                if not g:
                    continue
                ax.scatter([r[key] for r in g],[r['delta'] for r in g],color=palette(i),marker=marker,s=32,
                    label=cid.replace('_docs','').replace('_website','').replace('_',' ') if marker=='o' or not any(r['resolved'] for r in group) else None)
    for ax,label in zip(axes,['Baseline mass on cycle endpoints','Baseline energy on cycle edges']):
        ax.set_xscale('log')
        ax.xaxis.set_major_locator(ticker.LogLocator(base=10,numticks=5))
        ax.xaxis.set_minor_locator(ticker.NullLocator())
        ax.margins(x=.10)
        ax.set_yscale('symlog',linthresh=1e-8)
        ax.axhline(0,color='gray',linewidth=.6)
        ax.axhspan(-1e-8,1e-8,color='gray',alpha=.12)
        ax.set_xlabel(label)
        ax.grid(alpha=.2)
    axes[0].set_ylabel('Signed Δλ₁ at q = 0.25')
    axes[1].legend(fontsize=7,loc='center left',bbox_to_anchor=(1,0.5))
    fig.suptitle('All qualified matched pairs; × unresolved, ○ resolved; gray band ±10⁻⁸',fontsize=10)
    fig.tight_layout()
    fig.savefig(directory/'response-localization.png',dpi=180,bbox_inches='tight')
    fig.savefig(directory/'response-localization.pdf',bbox_inches='tight')
    plt.close(fig)


def report(e,primary,cards):
    s=e['summary']
    rows=['# Sprint 23b: matched magnetic structural-attribution coverage','',
        f"October 31, 2026. Offline exploratory study; zero model/outbound calls. Evidence: [run JSON](../evidence/magnetic-attribution-23b-run-v1-{DATE}.json).",
        '',f"All fourteen frozen snapshots are accounted for. {s['selected_pairs']} cycles were selected in {s['corpora_with_selected_cycles']} corpora; {s['matched_pairs']} pass the matched controls and {s['qualified_primary_pairs']} qualify numerically at q=0.25. {s['resolved_primary_pairs']} changes resolve in {s['resolved_primary_corpora']} corpora.",
        '', '## Coverage and primary responses', '',
        'S/M/Q/R = selected/matched/qualified/resolved. Search entries for lengths 3/4/5/6 give candidate count plus E (exhaustive) or C (capped). Each length has an independent 250,000-expansion budget and two-cycle quota. Signed ranges and median absolute deltas include unresolved pairs.', '',
        '| Corpus | Full→view nodes (coverage) | Candidates/status 3/4/5/6 | S/M/Q/R | Signed Δ median [Q1,Q3]; range | Absolute Δ median [Q1,Q3]; range | Effective support fraction |',
        '|---|---|---|---|---|---|---|']
    for c in e['corpora']:
        p=c['per_q']['0.25']; d=p['signed_delta']; ab=p['absolute_delta']
        search=' / '.join(str(v['candidates'])+('E' if v['status']=='exhaustive' else 'C') for v in c['search'].values())
        rows.append(f"| {c['corpus_id']} | {c['full_nodes']}→{c['view_nodes']} ({100*c['node_coverage']:.1f}%) | {search} | {p['selected']}/{p['matched']}/{p['qualified']}/{p['resolved']} | {fmt(d['median'])} [{fmt(d['q1'])},{fmt(d['q3'])}]; {fmt(d['min'])}…{fmt(d['max'])} | {fmt(ab['median'])} [{fmt(ab['q1'])},{fmt(ab['q3'])}]; {fmt(ab['min'])}…{fmt(ab['max'])} | {100*p['baseline_effective_support_fraction']:.2f}% |")
    nonzero=[c for c in e['corpora'] if c['per_q']['0.25']['resolved']]
    large=max(nonzero,key=lambda c:c['per_q']['0.25']['absolute_relative_delta']['max'])
    lp=large['per_q']['0.25']
    others=[c for c in nonzero if c is not large]
    largest_other=max(c['per_q']['0.25']['absolute_relative_delta']['max'] for c in others)
    rows+=['','## Magnitude and localization interpretation','',
        f"The largest selected relative response is {100*lp['absolute_relative_delta']['max']:.2f}% on {large['corpus_id']}'s {large['view_nodes']}-node view ({100*large['node_coverage']:.1f}% of its snapshot). Its baseline effective support is {100*lp['baseline_effective_support_fraction']:.1f}% of that view. Across the other {len(others)} corpora with resolved responses, the largest selected relative response is {100*largest_other:.3f}%; their baseline modes have effective support below 10% of the view. Thus the broader study extends the named examples beyond Docker/React, including one less concentrated case, while most resolved responses remain small and localized. One selected cycle on a small view does not establish a typical response or practical importance.",
        '',f"Giving each of the 12 eligible corpora equal weight, the mean within-corpus resolved fraction is {100*e['equal_corpus_summary']['0.25']['mean_resolved_fraction']:.1f}% at q=.25 and {100*e['equal_corpus_summary']['0.1']['mean_resolved_fraction']:.1f}% at q=.10. These are descriptive summaries of the selected frames, not estimates of prevalence in documentation corpora.",
        '','## Fixed q=0.10 companion','','| Corpus | Qualified/resolved | Signed Δ median [Q1,Q3]; range | Absolute Δ median [Q1,Q3]; range |','|---|---|---|---|']
    for c in e['corpora']:
        p=c['per_q']['0.1'];d=p['signed_delta'];a=p['absolute_delta']
        rows.append(f"| {c['corpus_id']} | {p['qualified']}/{p['resolved']} | {fmt(d['median'])} [{fmt(d['q1'])},{fmt(d['q3'])}]; {fmt(d['min'])}…{fmt(d['max'])} | {fmt(a['median'])} [{fmt(a['q1'])},{fmt(a['q3'])}]; {fmt(a['min'])}…{fmt(a['max'])} |")
    rows+=['','The primary signed medians/IQRs and absolute ranges, relative distributions, full per-pair diagnostics and expansion counts are in the evidence and all-pair JSON. Relative changes are unavailable when the baseline is below its resolution floor. Lambda2 is retained as a descriptive ordered-eigenvalue companion; multiplicity and crossings prevent interpreting it as a tracked physical mode.',
        '',f"Both charges resolve {s['both_q_resolved_pairs']} pairs; {s['q_sign_disagreements_both_resolved']} of those disagree in sign. No q was selected by outcome.",
        '', '## Controls, qualification and accounting','',
        f"All q=0 matched deltas are exactly zero after explicit matrix-equality reuse: {s['q0_all_exact_zero']}. Global reversal structural/numerical/invariance controls pass across all fourteen: {s['global_controls_all_pass']}. Baselines reproduce Sprint 22 within {s['baseline_s22_max_abs_diff']:.3g}; the historical three-anchor replay reproduced every old cycle and control, and its spectra within the recorded 1e-12 tolerance.",
        '',f"Worst ground residual {s['worst_ground_residual']:.3g}; mass-sum maximum error {s['mass_sum_max_abs_error']:.3g}. Each of the lowest three modes has an absolute residual bounded by the saved full-eigenbasis Frobenius residual (worst bound {s['worst_low_mode_absolute_residual_bound']:.3g}); individual lambda2/lambda3 residuals were not exported. Exact ground-cluster residuals govern resolution. Energy identity failures: {s['energy_identity_failures']}. Finite-change accounts: {s['finite_change_computed']} computed, {s['finite_change_unavailable']} unavailable, {s['finite_change_failures']} identity/relaxation failures (all charges). A clustered or unqualified ground mode omits the scalar account and retains projector diagnostics; these accounts do not identify independent causal effects per hyperlink.",
        '', f"Selected overlap with the original twelve interventions: {s['previously_examined_selected']} previously examined, {s['newly_examined_selected']} newly examined. The overlap companion is descriptive; neither subset is independent confirmation. No pair was replaced after outcome inspection.",
        '', 'Searches sample encountered candidates. Capped-empty cells mean none found within the search budget; exhaustive-empty cells mean no eligible cycle of that length. No unused quota transfers. Fourteen convenience snapshots and multiple edits sharing one graph do not justify a pooled significance test. Equal-corpus summaries state their eligible denominator in the JSON.',
        '', '## Localization and case cards','',
        'The figure plots every qualified primary pair against baseline cycle mass and energy, preserving corpus identity and unresolved responses. Mass/energy are diagnostics measured after selection; localized support can explain insensitivity to some edits but does not prove every null response. Cycle length, degree-volume share and edited W share are retained per pair. Projector fields describe the complete ground cluster; no arbitrary vector in a repeated eigenspace is interpreted.',
        '', '![All responses and baseline localization](../../../artifacts/magnetic-attribution-23b-v1/figures/response-localization.png)',
        '', 'Cards use the first frozen length/selection-hash cycle for each corpus. The JSON records original IDs/arcs and all attribution fields. No source-page interpretation was performed.', '']
    for c in cards:
        if 'cycle' not in c:
            rows.append(f"- **{c['corpus_id']}**: no eligible selected case; see exhaustive/capped coverage above.")
        else:
            r=next(r for r in c['q_results'] if r['q']==.25)
            rows.append(f"- **{c['corpus_id']}**: length {r['length']}, mass {fmt(r['baseline_cycle_mass'])}, edge energy {fmt(r['baseline_cycle_energy'])}, Δ {fmt(r['delta'])}, resolved {r['resolved']}; IDs/arcs in `analysis/case-cards.json`.")
    rows+=['','## Manuscript-inclusion proposal (author decision pending)','',
        f"Replace the three-anchor matched-reversal table with the compact table below; retain at most the localization figure in the supporting artifact unless space permits. Proposed wording: ‘Across fourteen frozen documentation snapshots, topology-only sampling selected {s['selected_pairs']} individual cycle reversals in {s['corpora_with_selected_cycles']} corpora. At q=0.25, {s['resolved_primary_pairs']} changes in {s['resolved_primary_corpora']} corpora exceeded the numerical resolution threshold while preserving the weighted undirected graph, directed degrees, component partitions and trophic incoherence. Effect magnitudes and ground-mode localization varied by corpus. These exploratory observations establish sensitivity beyond the specified controls on the observed pairs; they do not establish practical usefulness.’ Include the observed ranges from the table, rather than presenting resolution as practical importance.",
        '', '| Corpus | Selected/resolved (.25) | Absolute Δ median; maximum | Maximum absolute relative change | Baseline effective support |','|---|---|---|---|---|']
    for c in e['corpora']:
        p=c['per_q']['0.25'];ab=p['absolute_delta'];rel=p['absolute_relative_delta']
        rows.append(f"| {c['corpus_id']} | {p['selected']}/{p['resolved']} | {fmt(ab['median'])}; {fmt(ab['max'])} | {fmt(100*rel['max'])+'%' if rel['max'] is not None else 'NA'} | {100*p['baseline_effective_support_fraction']:.2f}% |")
    rows+=['','Adoption map: replace `git/paper/tables/w02-matched-summary.tex`; revise the sampling paragraph in `sections/03-evaluation-design.tex` and the second subsection of `sections/04-structural-validation.tex` together. Preserve the approved introduction. No page-fit result is claimed because this sprint does not modify or compile the manuscript.',
        '', 'The original Sprint 23 conclusion remains scoped to its selected Docker/React/GitHub triangles. This extension uses a different encountered candidate frame and several cycle lengths; differences between studies are not a controlled selection-method comparison. Full old/new accounting remains in the artifact. Keep the negative Sprint 31c AI-purpose result there as directed, without a page-purpose paper section. No submitted manuscript, bibliography, tag or public artifact was changed.',
        '', '## Resources and reproduction','',
        f"RA `{e['ra_commit']}`; image `{e['image']['reference']}`. New real-graph measure time {s['production_measure_seconds']:.1f} seconds, generated files at analysis {s['generated_bytes']/1024**2:.1f} MiB. Containers use two CPUs, 4 GiB and single-threaded BLAS, network disabled. Per-run wall times, module/input/output hashes and runtime versions are in the JSON. Host and container qualification are distinct; no arm64/macOS/HPC run is claimed.",
        '', 'Commands: `python -B git/context/scripts/magnetic_attribution_23b.py qualify-inputs`, `qualify-run`, `freeze`; commit plan and selection, then `measure`. Run this reporting script in the pinned image. Measurement resumes only from verified complete receipts. Historical inputs and the operator module remain byte-identical, checked in the evidence.']
    target=CTX/f'reports/{DATE}-magnetic-attribution-23b-results.md'
    target.write_bytes(('\n'.join(rows)+'\n').encode())


if __name__=='__main__':
    analyze()
