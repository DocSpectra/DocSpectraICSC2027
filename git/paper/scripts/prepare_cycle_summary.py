"""Format frozen Sprint 23b summaries; no new experiment or statistical analysis."""
import hashlib
import json
from pathlib import Path

paper = Path(__file__).resolve().parents[1]
root = paper.parents[1]
source = root / 'git/context/evidence/magnetic-attribution-23b-run-v1-2026-10-31.json'
data = json.loads(source.read_text(encoding='utf-8'))
names = {
    'agopengps': 'AgOpenGPS', 'ansible_docs': 'Ansible',
    'argo_cd_docs': 'Argo CD', 'django_docs': 'Django',
    'docker_docs': 'Docker', 'elasticsearch_docs': 'Elasticsearch',
    'express_docs': 'Express', 'github_docs': 'GitHub',
    'kubernetes_website_docs': 'Kubernetes', 'nextjs_docs': 'Next.js',
    'pytorch_docs': 'PyTorch', 'react_docs': 'React',
    'redis_docs': 'Redis', 'vscode_docs': 'VS Code',
}

def compact(value):
    return f'{value:.3g}'.replace('e-0', 'e-').replace('e+0', 'e+')

rows, ledger = [], []
assert len(data['corpora']) == 14
for i, corpus in enumerate(data['corpora']):
    key = corpus['corpus_id']
    q = corpus['per_q']['0.25']
    selected, resolved = q['selected'], q['resolved']
    assert selected == q['matched'] == q['qualified']
    median = q['absolute_delta']['median']
    relative = q['absolute_relative_delta']['max']
    support = 100 * q['baseline_effective_support_fraction']
    med_display = compact(median) if selected else '---'
    max_display = compact(100 * relative) + r'\%' if selected and relative is not None else '---'
    support_display = f'{support:.2f}' + r'\%'
    rows.append(f'{names[key]} & {resolved}/{selected} & {med_display} & {max_display} & {support_display}' + r' \\')
    ledger.append({
        'corpus_id': key, 'evidence_path': ['corpora', i, 'per_q', '0.25'],
        'selected': selected, 'resolved': resolved,
        'median_absolute_delta': median, 'median_display': med_display,
        'maximum_absolute_relative_delta': relative, 'maximum_display': max_display,
        'baseline_effective_support_fraction': q['baseline_effective_support_fraction'],
        'support_display': support_display,
    })

table = r'''\begin{table}[t]
\caption{Selected cycle reversals at $q=.25$: R/S is resolved/selected.
Dashes denote no selected pairs; medians include unresolved changes.
Maximum relative change is $100|\Delta\lambda_1|/\lambda_1^{\rm baseline}$.
Effective support is a percentage of LWCC nodes; maxima are not typical responses.}
\label{tab:matched}
\centering
\begin{tabular}{@{}lrrrr@{}}
\toprule
Corpus & R/S & \shortstack{Median\\$|\Delta\lambda_1|$} & \shortstack{Max. relative\\change} & \shortstack{Effective\\support}\\
\midrule
''' + '\n'.join(rows) + r'''
\bottomrule
\end{tabular}
\end{table}
'''
(paper / 'tables/w02-matched-summary.tex').write_text(table, encoding='utf-8', newline='\n')
(paper / 'cycle-summary-provenance.json').write_text(json.dumps({
    'source': source.relative_to(root).as_posix(),
    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'summary': 'Frozen per-corpus primary-charge summaries, including unresolved pairs and both empty frames; no new inference.',
    'table_sha256': hashlib.sha256(table.encode()).hexdigest(),
    'rows': ledger,
}, indent=2) + '\n', encoding='utf-8', newline='\n')
print('Prepared fourteen corpus rows from the frozen 77-pair study.')
