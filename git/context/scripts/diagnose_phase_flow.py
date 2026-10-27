"""Frozen historical phase audit; no corpus writes or model calls."""
import csv
import hashlib
import json
import random
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
PIN = json.loads((ROOT / 'git/context/evidence/docker-corpus-variant-input-v2-2026-09-30.json').read_text())

def historical():
    for pin in PIN['code'].values():
        assert hashlib.sha256(Path(pin['path']).read_bytes()).hexdigest()==pin['sha256']
    path = Path(PIN['code']['metrics']['path'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == PIN['code']['metrics']['sha256']
    sys.path.insert(0, str(path.parent.parent))
    from docspectra_importer import graph_metrics
    return graph_metrics

def matrix(nodes, edges, q=.25):
    idx = {v:i for i,v in enumerate(nodes)}
    a = np.zeros((len(nodes),len(nodes)))
    for s,t in edges:
        if s != t and s in idx and t in idx:
            a[idx[s],idx[t]] = 1
    w = (a+a.T)/2
    return np.diag(w.sum(axis=1))-w*np.exp(2j*np.pi*q*(a-a.T))

def signs(nodes, edges, v, k=10):
    phases = np.array([np.angle(v[:,j]*np.exp(-1j*np.angle(v[:,j].sum()))) for j in range(1,min(k+1,v.shape[1]))])
    node = phases.mean(axis=0)
    idx = {v:i for i,v in enumerate(nodes)}
    delta = np.array([np.angle(np.exp(1j*(node[idx[t]]-node[idx[s]]))) for s,t in edges])
    return delta, np.where(delta>=0,1,-1), phases

def docker_graphs():
    gm=historical()
    root=ROOT/'artifacts/docker-temporal-2026-05-01-to-05-26-v2'
    rec=json.loads((root/'export-receipt.json').read_text())
    for s in rec['snapshots']:
        folder=root/Path(s['snapshot_path']).name
        nodes=[p.doc_id for p in gm.discover_markdown_pages(folder/'source',faq_patterns=['*faq*']) if not p.is_faq]
        path=folder/'dataset/graph_edges.csv'
        edges=[(r['source'],r['target']) for r in csv.DictReader(path.open(encoding='utf-8',newline=''))]
        chosen=gm.largest_weak_component(nodes,edges)
        yield s['commit'], [n for n in nodes if n in chosen], [(s,t) for s,t in edges if s in chosen and t in chosen], path

def main():
    out=Path(sys.argv[1]); out.mkdir(parents=True,exist_ok=False)
    gm=historical(); rows=[]
    for commit,nodes,edges,path in docker_graphs():
        l=matrix(nodes,edges); vals,v=np.linalg.eigh(l)
        d,sg,p=signs(nodes,edges,v)
        row={'commit':commit,'graph_csv_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'nodes':len(nodes),'edges':len(edges),
             'historical_flow':float(abs(sg.sum())/len(edges)), 'selected_eigenvalues':vals[:12].tolist(),
             'anchor_sum_magnitudes':np.abs(v[:,1:11].sum(axis=0)).tolist(),
             'minimum_node_amplitude_per_mode':np.min(abs(v[:,1:11]),axis=0).tolist(),
             'residual_scaled':float(np.linalg.norm(l@v-v*vals,'fro')/max(1,np.linalg.norm(l,'fro'))),
             'orthonormality_fro':float(np.linalg.norm(v.conj().T@v-np.eye(len(nodes)),'fro')),
             'hermitian_fro':float(np.linalg.norm(l-l.conj().T,'fro')),
             'near_zero_edges':[{'edge':e,'delta':float(x)} for e,x in zip(edges,d) if abs(x)<1e-10], 'permutations':[]}
        for seed in (7,19):
            perm=list(range(len(nodes))); random.Random(seed).shuffle(perm)
            ns=[nodes[i] for i in perm]; lp=matrix(ns,edges); ep,vp=np.linalg.eigh(lp)
            inverse=np.argsort(perm); restored=vp[inverse,:]
            # Evaluate anchoring in native solver row order; restoring first
            # changes floating summation order and can itself flip zero edges.
            dp,sp,pp=signs(ns,edges,vp)
            changes=[{'edge':e,'baseline_delta':float(a),'permuted_delta':float(b),'baseline_sign':int(sa),'permuted_sign':int(sb)} for e,a,b,sa,sb in zip(edges,d,dp,sg,sp) if sa!=sb]
            idx={node:i for i,node in enumerate(nodes)}
            for changed in changes:
                a,b=[idx[x] for x in changed['edge']]
                changed['endpoint_amplitudes_by_mode']=[[float(abs(v[a,j])),float(abs(v[b,j]))] for j in range(1,11)]
                changed['edge_relative_phases_by_mode']=[float(np.angle(v[b,j]*v[a,j].conj())) for j in range(1,11)]
            row['permutations'].append({'seed':seed,'matrix_equivariance_fro':float(np.linalg.norm(lp-l[np.ix_(perm,perm)],'fro')),
                'eigenvalue_max_error':float(np.max(abs(ep-vals))), 'flow':float(abs(sp.sum())/len(edges)), 'changed_edges':changes,
                'restored_before_anchor_flow':gm.magnetic_phase_metrics(nodes,edges,restored,phase_k=10)['flow_imbalance'],
                'wrapped_node_phase_branch_crossings':int(np.count_nonzero(abs(pp[:,inverse]-p)>np.pi)),
                'projector_selected_fro_error':float(np.linalg.norm(v[:,1:11]@v[:,1:11].conj().T-restored[:,1:11]@restored[:,1:11].conj().T,'fro'))})
        rng=np.random.default_rng(2909); rotation=[]
        for _ in range(12):
            vv=v*np.exp(1j*rng.uniform(-np.pi,np.pi,len(nodes)))
            dd,ss,_=signs(nodes,edges,vv)
            rotation.append({'flow':float(abs(ss.sum())/len(edges)),'changed_edges':int(np.count_nonzero(ss!=sg)),'max_delta_change':float(np.max(abs(dd-d)))})
        row['fixed_vectors_global_rotation_trials']=rotation
        rows.append(row); print(json.dumps({'commit':commit,'flow':row['historical_flow'],'changes':[len(p['changed_edges']) for p in row['permutations']]}),flush=True)
    (out/'diagnosis.json').write_text(json.dumps({'numpy':np.__version__,'historical_code_sha256':PIN['code']['metrics']['sha256'],'rows':rows},indent=2)+'\n')

if __name__=='__main__': main()
