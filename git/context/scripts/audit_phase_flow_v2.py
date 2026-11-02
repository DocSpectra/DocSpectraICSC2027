"""Offline frozen Docker qualification and optional recovered-anchor migration."""
import argparse
import csv
from dataclasses import asdict, replace
import hashlib
import io
import json
import os
import contextlib
from pathlib import Path
import platform
import random
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'git/RetrievalArena'))
from retrieval_arena.graph.magnetic_phase import PhaseConfig, measure, measure_subspace, subspace_edge_metrics, magnetic_matrix, relative_phase_counts, digest
from diagnose_phase_flow import docker_graphs, historical

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,data):
    if path.exists(): raise ValueError('output-exists:'+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def qualify(nodes,edges,subspace=False):
    measurement=measure_subspace if subspace else measure
    cfg=PhaseConfig(); l=magnetic_matrix(nodes,edges); e,v=np.linalg.eigh(l)
    r=measurement(nodes,edges,cfg,eigenpairs=(e,v)); checks=[]
    def check(name,rr):
        equal=rr['status']==r['status'] and rr['validity_reasons']==r['validity_reasons']
        if r['value'] is not None:
            equal=equal and rr['value'] is not None and abs(rr['value']-r['value'])<=1e-12
        checks.append({'check':name,'pass':bool(equal),'status':rr['status'],'value':rr['value'],
                       'minimum_coverage':rr.get('minimum_edge_coverage')})
    upper=min(cfg.modes+1,len(nodes)); baseline_signs=relative_phase_counts(nodes,edges,v[:,1:upper],cfg)[1]
    for seed in (7,19):
        p=list(range(len(nodes))); random.Random(seed).shuffle(p)
        nn=[nodes[i] for i in p]; lp=magnetic_matrix(nn,edges); ep,vp=np.linalg.eigh(lp)
        rr=measurement(nn,edges,cfg,eigenpairs=(ep,vp)); check('node_permutation_'+str(seed),rr)
        checks[-1].update(matrix_equivariance_fro=float(np.linalg.norm(lp-l[np.ix_(p,p)],'fro')),
                         eigenvalue_error=float(np.max(abs(ep-e))),
                         residual=rr.get('residual_relative'),orthogonality=rr.get('orthogonality_fro'))
        restored=vp[np.argsort(p),:]
        signs=relative_phase_counts(nodes,edges,restored[:,1:upper],cfg)[1]
        checks[-1]['changed_edge_mode_classifications']=int(np.count_nonzero(np.array(signs)!=baseline_signs))
        checks[-1]['pass'] &= checks[-1]['matrix_equivariance_fro']<=1e-12 and (subspace or checks[-1]['changed_edge_mode_classifications']==0)
    check('edge_input_reversal',measurement(nodes,list(reversed(edges)),cfg,eigenpairs=(e,v)))
    rng=np.random.default_rng(909)
    for trial in range(12):
        rotated=v*np.exp(1j*rng.uniform(-np.pi,np.pi,len(nodes)))
        if subspace and r['status']=='computed':
            rr=dict(r,value=subspace_edge_metrics(nodes,edges,rotated[:,:r['actual_modes']])['value'])
        else:
            rr=measurement(nodes,edges,cfg,eigenpairs=(e,rotated))
        check('independent_global_phase_'+str(trial),rr)
    if subspace and r['status']=='computed':
        # Any unitary change of basis of the selected span leaves its
        # projector invariant; mix only actual repeated eigenspaces here
        # so the supplied columns remain valid eigenpairs as well.
        vv=v.copy(); start=0
        while start<len(e):
            end=start+1
            while end<len(e) and abs(e[end]-e[start])<1e-12: end+=1
            if end-start>1:
                z=rng.normal(size=(end-start,end-start))+1j*rng.normal(size=(end-start,end-start))
                unitary=np.linalg.qr(z)[0]; vv[:,start:end]=v[:,start:end]@unitary
            start=end
        check('repeated_eigenspace_unitary',measurement(nodes,edges,cfg,eigenpairs=(e,vv)))
    # Predeclared order-of-magnitude sweeps; never tuned against answer scores.
    for factor in (.1,10):
        cc=replace(cfg,gap_relative=cfg.gap_relative*factor,amplitude_relative=cfg.amplitude_relative*factor,phase_absolute=cfg.phase_absolute*factor)
        check('tolerance_factor_'+str(factor),measurement(nodes,edges,cc,eigenpairs=(e,v)))
    return {'receipt':r,'checks':checks,'numerically_qualified':r['status']=='computed' and all(c['pass'] for c in checks),
            'optimization_admitted':False,'scientific_utility':'unvalidated'}

def freeze_graph(folder,nodes,edges,path):
    write(folder/'nodes.json',nodes)
    text=io.StringIO(newline=''); writer=csv.writer(text,lineterminator='\n'); writer.writerow(['source','target']); writer.writerows(edges)
    (folder/'selected-edges.csv').write_bytes(text.getvalue().encode())
    return {'graph_view':'retrieval_no_faq_lwcc','source_graph_csv':str(path),'source_graph_csv_sha256':sha(path),
            'frozen_nodes_sha256':sha(folder/'nodes.json'),'frozen_selected_edges_sha256':sha(folder/'selected-edges.csv'),
            'node_policy':'historical_exporter_page_order_non_faq_lwcc','edge_policy':'binary_simple_directed_induced_lwcc'}

def anchor_graphs():
    roster=ROOT/'git/context/evidence/cross-corpus-roster-recovered-v4-2026-09-28.json'
    gm=historical()
    for row in json.loads(roster.read_text())['rows']:
        if not row['paper_member']: continue
        recpath=ROOT/row['export_receipt_path']; rec=json.loads(recpath.read_text())
        if sha(recpath)!=row['export_receipt_sha256']: raise ValueError('anchor-receipt-hash-mismatch')
        snap=next(s for s in rec['snapshots'] if s['commit']==row['cikm_anchor_commit'])
        folder=ROOT/snap['snapshot_path']
        path=folder/'dataset/graph_edges.csv'
        source=folder/'source'/snap['relative_scope']
        ns=[p.doc_id for p in gm.discover_markdown_pages(source,faq_patterns=row['faq_exclusion_patterns']) if not p.is_faq]
        if len(ns)!=snap['manifest_counts']['num_corpus_pages']:
            raise ValueError('anchor-node-count-mismatch:'+row['corpus_id'])
        es=[(r['source'],r['target']) for r in csv.DictReader(path.open(newline='',encoding='utf-8'))]
        chosen=gm.largest_weak_component(ns,es)
        yield row,[n for n in ns if n in chosen],[(s,t) for s,t in es if s in chosen and t in chosen],path

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True); parser.add_argument('--anchors',action='store_true'); parser.add_argument('--subspace',action='store_true'); parser.add_argument('--reuse-from',type=Path); args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False); gm=historical(); rows=[]
    from retrieval_arena.graph import magnetic_phase
    (args.output/'magnetic_phase.py').write_bytes(Path(magnetic_phase.__file__).read_bytes())
    config_output=io.StringIO()
    with contextlib.redirect_stdout(config_output): np.show_config()
    write(args.output/'runtime.json',{'python':sys.version,'executable':sys.executable,'platform':platform.platform(),
                                   'numpy':np.__version__,'numpy_build':config_output.getvalue(),
                                   'thread_environment':{k:os.getenv(k) for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS')}})
    def reuse(relative,ns,es,inputs):
        if args.reuse_from is None: return None
        source=args.reuse_from/relative
        if not source.exists(): return None
        prior=json.loads(source.read_text()); rr=prior['receipt']
        expected=digest({'nodes':sorted(ns),'edges':sorted(set(es))})
        if rr.get('code_sha256')!=sha(Path(magnetic_phase.__file__)) or rr.get('graph_sha256')!=expected or rr.get('config_sha256')!=digest(asdict(PhaseConfig())):
            raise ValueError('reuse-code-graph-config-mismatch')
        for name in ('nodes.json','selected-edges.csv'):
            if sha(source.parent/name)!=sha(args.output/relative.parent/name):
                raise ValueError('reuse-frozen-input-mismatch')
        prior['reused_receipt_sha256']=sha(source); prior['reused_from']=str(source)
        return prior
    for commit,ns,es,path in docker_graphs():
        folder=args.output/commit[:12]; inputs=freeze_graph(folder,ns,es,path)
        result=reuse(Path(commit[:12])/'receipt.json',ns,es,inputs) or qualify(ns,es,args.subspace)
        result.update(commit=commit,inputs=inputs)
        write(folder/'receipt.json',result); rows.append(result)
        print(json.dumps({'commit':commit,'qualified':result['numerically_qualified'],'value':result['receipt']['value'],'failed':[c for c in result['checks'] if not c['pass']]}),flush=True)
    result={'schema_version':'docspectra.icsc.phase_flow_qualification.v2','python':platform.python_version(),'numpy':np.__version__,
            'thread_environment':{k:os.getenv(k) for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS')},
            'audit_code_sha256':sha(Path(__file__)),'historical_diagnosis_code_sha256':sha(Path(__file__).with_name('diagnose_phase_flow.py')),
            'input_manifest_sha256':sha(ROOT/'git/context/evidence/docker-corpus-variant-input-v2-2026-09-30.json'),
            'all_docker_qualified':all(r['numerically_qualified'] for r in rows),'rows':rows,'migration':[]}
    if args.anchors and result['all_docker_qualified']:
        for row,ns,es,path in anchor_graphs():
            cid=row['corpus_id']; folder=args.output/'anchors'/cid; inputs=freeze_graph(folder,ns,es,path)
            # Keep dense full-eigensystem semantics; do not silently replace
            # it with a partial sparse solver for large corpora.
            if len(ns)>2400:
                rr={'status':'unavailable','value':None,'validity_reasons':['dense_audit_resource_cap_2400_nodes']}; qual=False; old=None
                audit={'receipt':rr,'numerically_qualified':False,'checks':[]}
            else:
                relative=Path('anchors')/cid/'audit.json'
                audit=reuse(relative,ns,es,inputs) or qualify(ns,es,args.subspace); rr=audit['receipt']; qual=audit['numerically_qualified']
                prior_migration=args.reuse_from/'anchors'/cid/'migration.json' if args.reuse_from else None
                if audit.get('reused_from') and prior_migration and prior_migration.exists():
                    old=json.loads(prior_migration.read_text())['historical_recomputed_diagnostic']
                else:
                    old=gm.magnetic_phase_metrics(ns,es,np.linalg.eigh(magnetic_matrix(ns,es))[1],phase_k=10)['flow_imbalance']
            migrated={'corpus_id':cid,'anchor_commit':row['cikm_anchor_commit'],'inputs':inputs,
                      'historical_recomputed_diagnostic':old,'new_receipt':rr,'numerically_qualified':qual,
                      'optimization_admitted':False,'historical_values_unchanged':True}
            write(folder/'audit.json',audit); write(folder/'migration.json',migrated); result['migration'].append(migrated)
            print(json.dumps({'corpus':cid,'nodes':len(ns),'qualified':qual,'status':rr['status'],'reasons':rr['validity_reasons']}),flush=True)
    write(args.output/'summary.json',result)

if __name__=='__main__': main()
