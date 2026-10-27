"""Format the recorded matched-cycle results; no new experiment or inference."""
import hashlib
import json
from pathlib import Path

paper = Path(__file__).resolve().parents[1]
root = paper.parents[1]
source = root / 'git/context/evidence/magnetic-attribution-23-v1-2026-10-25.json'
data = json.loads(source.read_text(encoding='utf-8'))
rows = []
ledger = []
for key, name in [('docker_docs', 'Docker'), ('react_docs', 'React'), ('github_docs', 'GitHub')]:
    cases = [(i, row) for i, row in enumerate(data['table'])
             if row['case'].startswith(key + '/') and row['q'] == .25
             and row['control'] == 'matched_cycle_reversal']
    assert len(cases) == 4 and all(r['controls_passed'] for _, r in cases)
    count = sum(r['resolved'] for _, r in cases)
    largest_i, largest = max(cases, key=lambda pair: abs(pair[1]['relative_to_baseline']))
    maximum = 100 * abs(largest['relative_to_baseline'])
    support = 100 * cases[0][1]['a']['effective_support_fraction']
    assert all(r['a']['effective_support_fraction'] == cases[0][1]['a']['effective_support_fraction'] for _, r in cases)
    display = f'{maximum:.3f}\\%' if count else r'---'
    rows.append(f'{name} & {count}/4 & {display} & {support:.2f}\\%' + r' \\')
    ledger.append({'corpus': name, 'table_indices': [i for i,_ in cases],
                   'resolved': count, 'maximum_index': largest_i,
                   'maximum_absolute_percent': maximum, 'maximum_display': display,
                   'baseline_support_percent': support})
table = r'''\begin{table}[t]
\caption{Four selected cycle reversals per corpus at $q=.25$. Largest change is
$100|\Delta\lambda_1|/\lambda_1^{\rm baseline}$; a dash means none resolved.
Effective support is the baseline ground mode's support as a percentage of LWCC
nodes. Maxima describe these selected cycles, not a typical corpus response.}
\label{tab:matched}
\centering
\begin{tabular}{@{}lrrr@{}}
\toprule
Corpus & Resolved & Largest change & Effective support\\
\midrule
''' + '\n'.join(rows) + r'''
\bottomrule
\end{tabular}
\end{table}
'''
(paper / 'tables/w02-matched-summary.tex').write_text(table, encoding='utf-8', newline='\n')
(paper / 'cycle-summary-provenance.json').write_text(json.dumps({
    'source': str(source.relative_to(root)),
    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'summary': 'Counts and maxima over all four topology-selected cycles per corpus, not means or population estimates.',
    'rows': ledger,
}, indent=2) + '\n', encoding='utf-8', newline='\n')
print('Prepared three corpus rows from all twelve selected reversals.')
