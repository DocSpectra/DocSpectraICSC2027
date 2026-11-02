"""Present recorded results in tables; no scientific recomputation.

Run after prepare_w02_displays.py when regenerating the historical W02 displays.
This overlay owns matched/answers and three additional results tables.
"""
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json

PAPER = Path(__file__).resolve().parents[1]
ROOT = PAPER.parents[1]
old_ledger = json.loads((PAPER/'w02-prose-provenance.json').read_text(encoding='utf-8'))
sources = {k:v for k,v in old_ledger['sources'].items() if k in ('S18','S20','S22','S23','S24')}
data = {}
for key, source in sources.items():
    p = ROOT/source['path']
    assert hashlib.sha256(p.read_bytes()).hexdigest() == source['sha256'], key
    data[key] = json.loads(p.read_text(encoding='utf-8'))
ledger = []
derived_ledger = []
outputs = {}
def read(source,path):
    v=data[source]
    for key in path: v=v[key]
    return v
def show(source,path,places=3,signed=False,scale=1,absolute=False,scientific=False,boolean=False):
    value=read(source,path)
    if boolean:
        rendered='yes' if value else 'no'
        fmt='boolean'
    elif scientific:
        mantissa,exponent=f'{value:+.2e}'.split('e')
        rendered=rf'{mantissa}\!\times\!10^{{{int(exponent)}}}'
        fmt='3 significant figures'
    elif places is None:
        rendered=str(value); fmt='exact'
    else:
        n=Decimal(str(value))*Decimal(scale)
        if absolute: n=abs(n)
        rendered=format(n.quantize(Decimal(10)**-places,rounding=ROUND_HALF_UP),
                        ('+' if signed else '')+f'.{places}f')
        fmt=f'{places} decimals, half away from zero'
    ledger.append(dict(source=source,path=path,exact=value,display=rendered,
                       format=fmt,scale=scale,absolute=absolute))
    return rendered
def write(name,text):
    p=PAPER/'tables'/name
    p.write_text(text,encoding='utf-8',newline='\n')
    outputs[str(p.relative_to(PAPER)).replace('\\','/')]=hashlib.sha256(p.read_bytes()).hexdigest()

def graph_ratio(path, density=False):
    """Present elementary ratios of frozen full-graph counts; no graph rerun."""
    m = read('S22',path+['arcs'])
    n = read('S22',path+['nodes'])
    value = Decimal(m) / Decimal(n*(n-1) if density else n)
    shown = value * (100 if density else 1)
    digits = 3 if density else 2
    rendered = format(shown.quantize(Decimal(10)**-digits,rounding=ROUND_HALF_UP),'f')
    derived_ledger.append(dict(source='S22',paths=[path+['arcs'],path+['nodes']],
                               inputs={'arcs':m,'nodes':n},
                               formula='100*m/[N*(N-1)]' if density else 'm/N',
                               exact_decimal=str(shown),display=rendered,
                               format=f'{digits} decimals, half away from zero'))
    return rendered
def table(caption,label,columns,body):
    return (r'\begin{table}[t]'+'\n'+r'\caption{'+caption+'}\n'+r'\label{'+label+'}\n'+
            r'\centering'+'\n'+r'\begin{tabular}{@{}'+columns+r'@{}}'+'\n'+r'\toprule'+'\n'+
            body+'\n'+r'\bottomrule'+'\n'+r'\end{tabular}'+'\n'+r'\end{table}'+'\n')
def row(*items): return ' & '.join(items)+r'\\'
def panel(title,n=3): return r'\multicolumn{'+str(n)+r'}{@{}l@{}}{\textit{'+title+r'}}\\'
def math(s):return '$'+s+'$'
def match(case,q=.25):
    return next(i for i,v in enumerate(data['S23']['table']) if v['case']==case and v['q']==q)

# All twelve previously reported documentation pairs, with control magnitudes.
lines=[row('Pair',r'$q=.25$',r'$q=.10$','R'),r'\midrule']
for prefix,label,maxcase in [('docker_docs','Docker','cycle2'),('react_docs','React','cycle1'),('github_docs','GitHub',None)]:
    for cycle in range(1,5):
        case=f'{prefix}/cycle{cycle}'
        i,j=match(case),match(case,.1)
        lines.append(row(f'{label} {cycle}',
            math(show('S23',['table',i,'delta_lambda1'],scientific=True)),
            math(show('S23',['table',j,'delta_lambda1'],scientific=True)),
            show('S23',['table',i,'resolved'],boolean=True)))
    i=match(prefix+'/cycle1')
    eff=show('S23',['table',i,'a','effective_support_fraction'],4)
    tv=show('S23',['table',i,'a','degree_mass_tv_distance'])
    lines.append(r'\multicolumn{4}{@{}l@{}}{'+f'{label} baseline: effective fraction {eff}; TV {tv}'+r'}\\')
    if maxcase:
        v=show('S23',['table',match(prefix+'/'+maxcase),'relative_to_baseline'],scale=100,absolute=True)
        lines.append(r'\multicolumn{4}{@{}l@{}}{'+rf'Maximum relative $|\Delta\lambda_1|$ at $.25$: {v}\%'+r'}\\')
    lines.append(r'\addlinespace')
lines += [panel('Synthetic endpoints at $q=.25$',4),row('Case',r'$\lambda_1^A$',r'$\lambda_1^B$','')]
i=match('small/five_triangle')
lines.append(row('Five-node',show('S23',['table',i,'a','lambda1'],4),show('S23',['table',i,'b','lambda1'],4),''))
lines += [r'\addlinespace',panel('Finite-change terms at $q=.25$',4),row('Case','Fixed mode','Relaxation','')]
for case,label,sci in [('docker_docs/cycle1','Docker 1',True),('small/path_forest','Forest',False)]:
    i=match(case)
    lines.append(row(label,*[math(show('S23',['table',i,'finite_change',key],2,signed=True,scientific=sci))
                            for key in ['fixed_mode_term','relaxation_term']],''))
write('w02-matched.tex',table(
    r'Cycle reversals: LWCC $\Delta\lambda_1$; R denotes resolution at $q=.25$. The same eight pairs resolve at $.10$; six switch sign. Baseline effective-support fraction and degree-mass TV use $.25$. Controls show endpoints and Eq.~\ref{eq:change} terms.',
    'tab:matched','lrrc','\n'.join(lines)))

# Pairwise coefficients already recorded for the same five comparators; no new correlations.
lines=[row('Comparator',r'$\lambda_1(.25)$',r'$\lambda_2(.25)$'),r'\midrule',
       panel(r'Corpus panel: Spearman $\rho$, 14 anchors')]
for label,key in [(r'Matched connectivity $\lambda_2(0)$','weighted_q0_lambda2'),
                  ('Mean weighted degree','mean_weighted_degree'),('Out-degree Gini','outdegree_gini'),
                  ('Trophic incoherence','trophic_incoherence'),('Reciprocity','reciprocal_arc_fraction')]:
    lines.append(row(label,*[math(show('S22',['A_characterization','spearman',mode,key]))
                            for mode in ['lambda1_q0.25','lambda2_q0.25']]))
lines += [r'\addlinespace',panel(r'Exposure proxy: $\rho$ (Holm $p$), 29 variants/family')]
for family,label in [('main','Main'),('sensitivity','Scaffolding removed')]:
    vals=[]
    for mode in ['lambda1_q0.25','lambda2_q0.25']:
        p=['B_orientation','families',family,'tests_normalized','primary',mode]
        vals.append(math(show('S22',p+['rho'])+r'\;('+show('S22',p+['holm_p'])+')'))
    lines.append(row(label,*vals))
write('w02-correlations.tex',table(
    r'Spearman correlations of magnetic modes with corpus descriptors and the exposure proxy. Orientation variants are dependent; parentheses give within-family Holm-adjusted $p$-values.',
    'tab:correlations','lrr','\n'.join(lines)))

# Whole-corpus profiles and clearly separated historical/sensitivity/exploratory tests.
lines=[row('Arm',r'$n/N$',r'$m$',r'$I$',r'$\bar{k}$',r'$d$ (\%)',r'$\lambda_1$',r'$F_0$'),r'\midrule']
for arm in ['O','LT','R0']:
    p=['C_refinement','profiles','wc/'+arm]
    coverage=show('S22',p+['lwcc_nodes'],None)+'/'+show('S22',p+['nodes'],None)
    lines.append(row(arm,coverage,show('S22',p+['arcs'],None),show('S22',p+['isolates'],None),
                     graph_ratio(p),graph_ratio(p,density=True),
                     show('S22',p+['lambda1_q0.25'],4),show('S22',p+['trophic_incoherence'])))
lines += [r'\addlinespace',panel(r'Area-paired tests: sample size, mean, $p$',8)]
for label,source,p,precision in [
    ('Historical (unnormalized)','S18',['primary_test'],3),
    ('Exclude three second-chance areas (unnormalized)','S18',['SA1'],3),
    ('Normalized (exploratory)','S22',['C_refinement','normalized_exploratory_analogue','test'],6)]:
    values=', '.join([show(source,p+['n'],None),math(show(source,p+['statistic_mean'],2,signed=True)),
                      math(show(source,p+['p'],precision))])
    lines.append(r'\multicolumn{8}{@{}l@{}}{'+label+': '+values+r'}\\')
write('w02-refinement-summary.tex',table(
    r'Full-graph pages $N$, links $m$, isolates $I$, mean out-degree $\bar{k}=m/N$ and density $d=m/[N(N-1)]$. LWCC: $n$, $\lambda_1(.25)$ and $F_0$. Area means are LN-standardized LT--R0; rejection threshold $.05$.',
    'tab:refinement-summary','lrrrrrrr','\n'.join(lines)))

# Conditional follow counts and trace-level chain stay visibly separate.
lines=[row('Measure','LT','R0'),r'\midrule',panel('Accepted follows')]
values=[]
for arm in ['LT','R0']:
    p=['tables_all111',f't2/{arm}/s1-follow','follow_accepted']
    values.append(show('S24',p+['edge_class=added'],None)+'/'+show('S24',p+['n'],None))
lines.append(row('Added / all accepted',*values))
for label,key in [('Mean added-edge menu share','mean_menu_added_share'),('Added-edge use share','added_share_of_accepted_follows')]:
    lines.append(row(label,*[show('S24',['tables_all111',f't2/{arm}/s1-follow','opportunity_summary',key])
                            for arm in ['LT','R0']]))
lines += [r'\addlinespace',panel('Trace chain (111 follow-enabled FAQ traces/arm)')]
for label,key in [('Accepted added-edge follow','2_added_edge_follow'),
                  ('First destination-text exposure','3_first_page_text_exposure'),
                  ('Directly supported destination','4_destination_supported'),
                  ('Cited lines returned by that follow','7_cited_lines_from_this_follow')]:
    lines.append(row(label,*[show('S24',['chain_t2_follow_all111',arm,'traces',key],None) for arm in ['LT','R0']]))
write('w02-link-use.tex',table(
    r'Observed link use: accepted-action shares and trace-level chain. Support is judged at page level. All 1,368 saved traces passed identity/history checks.',
    'tab:link-use','lrr','\n'.join(lines)))

# Preserve all answer contrasts and append the lexical scoring-view comparison.
lines=[row('Contrast',r'\shortstack{Mean score\\difference}','95\\% interval'),r'\midrule']
groups=[('Completeness (1--5), 15 tasks',['tier1','tables','completeness'],['LT - O','R0 - O','LT - R0','S - O']),
        ('FactualCorrectness (0--1), 111 FAQs',['tier2','ragas','full_111','tables','factual_correctness/pooled'],['LT - O','R0 - O','LT - R0','S - O']),
        ('Lexical RR@10: direct judgment, common 105',['tier2','rows','primary','retrieval','lexical/rr'],['LT - O','S - O']),
        ('Lexical RR@10: moved/derived credit (O1), 111',['tier2','rows','lenient_o1_111','retrieval','lexical/rr'],['LT - O'])]
for title,p,contrasts in groups:
    lines.append(panel(title))
    for contrast in contrasts:
        q=p+[contrast]
        est=show('S20',q+['estimate'],signed=True)
        ci=[show('S20',q+['ci95',i],signed=True) for i in range(2)]
        lines.append(row(contrast.replace(' - ','--'),math(est),math('['+','.join(ci)+']')))
    lines.append(r'\addlinespace')
write('w02-answers.tex',table(
    r'Answer and lexical-retrieval differences in score units (first arm minus second). Positive favors the first arm; intervals spanning zero indicate no clear difference, not equivalence. Answer scores pool conditions; S--O is replay, not a noise bound.',
    'tab:answers','lrr','\n'.join(lines)))

manifest={'purpose':'Presentation of existing recorded results; no new scientific analysis',
          'sources':sources,'values':ledger,'derived_values':derived_ledger,'table_sha256':outputs,
          'selection':'All prior matched pairs and answer contrasts retained. Five existing corpus comparators shown for both magnetic modes; additional second-mode coefficients, test means and replay interval are copied from frozen fields.'}
(PAPER/'results-table-provenance.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
print(f'{len(outputs)} tables; {len(ledger)} source-field transcriptions; all source hashes verified.')

