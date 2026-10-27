"""W02 presentation only: transcribe frozen values; never run scientific analysis.

Requires reportlab. Run from any directory with the bundled document Python.
The JSON ledger records each displayed scalar, its field, and rounding.
Use --corpora-only to refresh Table II without regenerating other displays.
"""
import csv
import hashlib
import json
import math
import runpy
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

PAPER = Path(__file__).resolve().parents[1]
ROOT = PAPER.parents[1]
EVIDENCE = ROOT / 'git/context/evidence'
SOURCES = {
    's22': EVIDENCE / 'normalized-magnetic-22-v1-2026-10-25.json',
    's23': EVIDENCE / 'magnetic-attribution-23-v1-2026-10-25.json',
    's20': EVIDENCE / 'wc-analysis-20-v1-2026-10-24.json',
}
data = {key: json.loads(path.read_text(encoding='utf8')) for key, path in SOURCES.items()}
ledger = []

def record(source, field, value, shown, rounding):
    ledger.append(dict(source=str(SOURCES[source].relative_to(ROOT)).replace('\\','/'),
                       field=field, exact=value, displayed=shown, rounding=rounding))
    return shown

def number(source, field, value, places=3, signed=False):
    rounded = Decimal(str(value)).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)
    shown = format(rounded, ('+' if signed else '') + f'.{places}f')
    return record(source, field, value, shown, f'{places} decimals, half away from zero')

def scientific(source, field, value):
    mantissa, exponent = f'{value:+.2e}'.split('e')
    shown = rf'{mantissa}\!\times\!10^{{{int(exponent)}}}'
    return record(source, field, value, shown, '3 significant figures, scientific notation')

def write(name, text):
    (PAPER / name).parent.mkdir(parents=True, exist_ok=True)
    (PAPER / name).write_text(text, encoding='utf8', newline='\n')

names = {'agopengps':'AgOpenGPS','ansible':'Ansible','argo_cd':'Argo CD','django':'Django',
         'docker':'Docker','elasticsearch':'Elasticsearch','express':'Express','github':'GitHub',
         'kubernetes_website':'Kubernetes','nextjs':'Next.js','pytorch':'PyTorch','react':'React',
         'redis':'Redis','vscode':'VS Code'}
corpus_rows = {}
for i, row in enumerate(data['s22']['A_characterization']['table']):
    base = f'A_characterization.table[{i}]'
    coverage = '/'.join(record('s22', base+'.'+k, row[k], str(row[k]), 'integer')
                        for k in ['lwcc_nodes','nodes_full'])
    vals = [number('s22',base+'.'+k,row[k],4) for k in
            ['lambda1_q0.25','lambda2_q0.25','weighted_q0_lambda2']]
    name = names[row['graph_id'].split('/')[-1].removesuffix('_docs')]
    corpus_rows[name] = name+' & '+coverage+' & '+' & '.join(vals)+r' \\'
groups = [
    ('Operational setup and configuration', ['Docker', 'Kubernetes', 'Ansible', 'Argo CD']),
    ('Framework learning and reference', ['Django', 'Express', 'React', 'Next.js']),
    ('Data management and administration', ['Elasticsearch', 'Redis']),
    ('Product workflows and development', ['GitHub', 'VS Code']),
    ('Library APIs and usage', ['PyTorch']),
    ('Hardware assembly and software setup', ['AgOpenGPS']),
]
assert len(corpus_rows) == 14
assert sorted(n for _, members in groups for n in members) == sorted(corpus_rows)
rows = []
for label, members in groups:
    rows.append(r'\multicolumn{5}{@{}l@{}}{\textit{'+label+r'}}\\')
    rows.extend(corpus_rows[name] for name in members)
write('tables/w02-corpora.tex',r'''\begin{table}[t]
\caption{Corpus profiles by documentation purpose. Read LWCC/full-inventory coverage ($n/N$) before comparing magnetic and undirected modes. All eigenvalues use the same LWCC and $W$. Purpose groups overlap; they are descriptive, not measured classes. Similar eigenvalues need not imply similar organization. Numerical zeros are printed as zero.}
\label{tab:corpora}
\centering
\begin{tabular}{@{}lrrrr@{}}
\toprule
& & & \multicolumn{2}{c}{Second-mode comparison}\\
\cmidrule(l){4-5}
Corpus & $n/N$ & $\lambda_1(.25)$ & Magnetic & Undirected\\
& & & $\lambda_2(.25)$ & $\lambda_2(0)$\\
\midrule
'''+ '\n'.join(rows)+r'''
\bottomrule
\end{tabular}
\end{table}
''')

if '--corpora-only' in sys.argv:
    print('Prepared grouped corpus table; all 14 rows retained.')
    raise SystemExit(0)

rows = []
table = data['s23']['table']
for prefix, label in [('docker_docs','Docker'),('react_docs','React'),('github_docs','GitHub')]:
    # IDs, not effect size, select all four declared documentation controls.
    matches = [(i,r) for i,r in enumerate(table) if r['q']==.25 and
               r['case'].startswith(prefix+'/') and r['control']=='matched_cycle_reversal']
    assert len(matches)==4, (prefix,len(matches))
    for i,r in matches:
        j,s = next((j,s) for j,s in enumerate(table) if s['case']==r['case'] and s['q']==.1)
        cycle = r['case'].split('cycle')[-1]
        a=scientific('s23',f'table[{i}].delta_lambda1',r['delta_lambda1'])
        b=scientific('s23',f'table[{j}].delta_lambda1',s['delta_lambda1'])
        status=record('s23',f'table[{i}].resolved',r['resolved'],'yes' if r['resolved'] else 'no','boolean')
        rows.append(f'{label} {cycle} & ${a}$ & ${b}$ & {status}'+r' \\')
    i,r=matches[0]
    eff=number('s23',f'table[{i}].a.effective_support_fraction',r['a']['effective_support_fraction'],4)
    tv=number('s23',f'table[{i}].a.degree_mass_tv_distance',r['a']['degree_mass_tv_distance'],3)
    rows.append(r'\multicolumn{4}{@{}l@{}}{'+label+rf' baseline: effective fraction {eff}; TV {tv}' + r'}\\')
    rows.append(r'\addlinespace')
write('tables/w02-matched.tex',r'''\begin{table}[t]
\caption{Every declared documentation cycle reversal: signed $\Delta\lambda_1$ on the unchanged LWCC. R denotes numerical resolution at $q=.25$; the same eight pairs resolve at $.10$. Effective fraction and TV describe the baseline ground mode at $.25$.}
\label{tab:matched}
\centering
\begin{tabular}{@{}lrrc@{}}
\toprule
Pair & $q=.25$ & $q=.10$ & R\\
\midrule
'''+ '\n'.join(rows)+r'''
\bottomrule
\end{tabular}
\end{table}
''')

rows=[]
for title,path in [('Completeness (1--5), 15 tasks',['tier1','tables','completeness']),
                   ('FactualCorrectness (0--1), 111 FAQs',['tier2','ragas','full_111','tables','factual_correctness/pooled'])]:
    table=data['s20']
    for key in path: table=table[key]
    rows.append(r'\multicolumn{3}{@{}l@{}}{'+title+r'}\\')
    for contrast in ['LT - O','R0 - O','LT - R0','S - O']:
        row=table[contrast]
        base='.'.join(path)+f'[{contrast}]'
        est=number('s20',base+'.estimate',row['estimate'],3,True)
        ci=[number('s20',base+f'.ci95[{i}]',v,3,True) for i,v in enumerate(row['ci95'])]
        rows.append(contrast.replace(' - ','--')+rf' & ${est}$ & $[{ci[0]},{ci[1]}]$'+r'\\')
    rows.append(r'\addlinespace')
write('tables/w02-answers.tex',r'''\begin{table}[t]
\caption{Selected automated answer endpoints, pooled over conditions. All five primary complex-task measures have arm--O intervals spanning zero; completeness is shown because it motivated the saved requirement audit. Intervals are paired 95\% bootstrap intervals, not equivalence tests. S--O is a replay contrast, not a noise bound.}
\label{tab:answers}
\centering
\begin{tabular}{@{}lrr@{}}
\toprule
Contrast & Estimate & Interval\\
\midrule
'''+ '\n'.join(rows)+r'''
\bottomrule
\end{tabular}
\end{table}
''')

# A compact vector diagram from the saved edge-energy CSV. No eigenproblem.
source_csv=ROOT/'artifacts/magnetic-attribution-23-v1/figures/fig-small-five-energy.csv'
energies=list(csv.DictReader(source_csv.open(encoding='utf8',newline='')))
assert len(energies)==12
figure=PAPER/'figures/w02-five-node.pdf'
figure.parent.mkdir(exist_ok=True)
c=canvas.Canvas(str(figure),pagesize=(252,143),invariant=1)
c.setTitle('Specified five-node magnetic control; frozen S23 edge energies')
c.setAuthor('')
blue=HexColor('#1765a3'); gray=HexColor('#4a5563')
for side,offset,label in [('a',0,'Original'),('b',128,'Triangle reversed')]:
    pos={k:(offset+x,y) for k,(x,y) in {'a':(24,79),'b':(100,79),'c':(62,119),'d':(24,22),'e':(100,22)}.items()}
    c.setFillColor(gray);c.setFont('Helvetica-Bold',8.5);c.drawCentredString(offset+62,133,label)
    for row in [r for r in energies if r['side']==side]:
        u,v=row['source'],row['target']; x1,y1=pos[u];x2,y2=pos[v]
        dx,dy=x2-x1,y2-y1;length=math.hypot(dx,dy);ux,uy=dx/length,dy/length
        sx,sy=x1+7*ux,y1+7*uy;tx,ty=x2-8*ux,y2-8*uy
        triangle=set((u,v))<={'a','b','c'}
        color=blue if triangle else gray
        value=float(row['edge_energy_q025']);c.setStrokeColor(color);c.setFillColor(color)
        c.setLineWidth(.5+20*value);c.line(sx,sy,tx,ty)
        p=c.beginPath();p.moveTo(tx,ty);p.lineTo(tx-5*ux+2.2*uy,ty-5*uy-2.2*ux);p.lineTo(tx-5*ux-2.2*uy,ty-5*uy+2.2*ux);p.close();c.drawPath(p,fill=1,stroke=0)
        # Fixed label positions avoid direction-dependent placement.
        pair=''.join(sorted((u,v)))
        lx,ly={'ab':(62,67),'ac':(29,104),'bc':(95,104),'ad':(7,49),'be':(112,49),'de':(62,8)}[pair]
        c.setFont('Helvetica',7.7);c.drawCentredString(offset+lx,ly,f'{value:.3f}'.lstrip('0'))
    for node,(x,y) in pos.items():
        c.setFillColor(HexColor('#ffffff'));c.setStrokeColor(gray);c.setLineWidth(.6);c.circle(x,y,6.5,fill=1,stroke=1)
        c.setFillColor(gray);c.setFont('Helvetica',8.5);c.drawCentredString(x,y-3,node)
c.showPage();c.save()
runpy.run_path(str(PAPER/'scripts/draw_refinement_comparison.py'), run_name='__main__')

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
manifest={'purpose':'Presentation only; no new scientific computation',
          'evidence':{k:{'file':str(p.relative_to(ROOT)).replace('\\','/'),'sha256':digest(p)} for k,p in SOURCES.items()},
          'figures':[
              {'output':'figures/w02-five-node.pdf','sha256':digest(figure),'input':str(source_csv.relative_to(ROOT)).replace('\\','/'),'input_sha256':digest(source_csv),'encoding':'arrow widths = 0.5 + 20 * recorded energy; labels 3 decimals; uniform node circles; fixed layout; both sides and all edges'},
              {'output':'figures/w02-refinement.pdf','sha256':digest(PAPER/'figures/w02-refinement.pdf'),'input':'git/context/evidence/normalized-magnetic-22-v1-2026-10-25.json','encoding':'Direct labels and shared scale; all 5 nulls/family/arm; see refinement-comparison-provenance.json'}],
          'table_values':ledger}
write('figures/w02-display-provenance.json',json.dumps(manifest,indent=2)+'\n')
print('Prepared 3 tables, 2 figures, and',len(ledger),'scalar ledger rows.')
