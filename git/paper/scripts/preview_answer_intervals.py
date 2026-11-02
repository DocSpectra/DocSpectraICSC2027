"""Render frozen answer intervals as a standalone preview or manuscript figure.

Requires ReportLab. Pass --manuscript for the native single-column figure.
Does not change the manuscript text or its supporting numerical table.
"""
import argparse
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import re
from reportlab.graphics.shapes import Drawing, Line, Circle, String
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics import renderPDF
from reportlab.lib.colors import HexColor

PAPER = Path(__file__).resolve().parents[1]
ROOT = PAPER.parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--manuscript', action='store_true')
args = parser.parse_args()
OUT = (PAPER / 'figures' / 'w02-answer-intervals.pdf' if args.manuscript else
       ROOT / 'drafts' / 'docspectra-icsc-2026-10-27-table-vi-interval-preview.pdf')
ledger = json.loads((PAPER / 'results-table-provenance.json').read_text())
source = ledger['sources']['S20']
raw = (ROOT / source['path']).read_bytes()
assert hashlib.sha256(raw).hexdigest() == source['sha256']
evidence = json.loads(raw)
all_contrasts = ['LT - O', 'R0 - O', 'LT - R0', 'S - O']
groups = [
    ('A  Completeness', '1-5 score; 15 tasks',
     ['tier1', 'tables', 'completeness'], all_contrasts,
     (-.35, .25), [-.3, -.2, -.1, 0, .1, .2]),
    ('B  FactualCorrectness', '0-1 score; 111 FAQs',
     ['tier2', 'ragas', 'full_111', 'tables', 'factual_correctness/pooled'], all_contrasts,
     (-.04, .04), [-.04, -.02, 0, .02, .04]),
    ('C  Lexical RR@10: direct judgment', 'Common set of 105 questions',
     ['tier2', 'rows', 'primary', 'retrieval', 'lexical/rr'], ['LT - O', 'S - O'],
     (-.13, .04), [-.12, -.08, -.04, 0, .04]),
    ('D  Lexical RR@10: moved/derived credit', 'O1 sensitivity; 111 questions',
     ['tier2', 'rows', 'lenient_o1_111', 'retrieval', 'lexical/rr'], ['LT - O'],
     (-.13, .04), [-.12, -.08, -.04, 0, .04]),
]
blue = HexColor('#235f8c')
ink = HexColor('#202a34')
gray = HexColor('#56616c')
light = HexColor('#dfe5ea')
d = Drawing(252, 187) if args.manuscript else Drawing(760, 540)
if args.manuscript:
    d.translate(0, -13)  # Trim unused bottom whitespace; preserve all label sizes.
def label(x, y, text, size=10, color=ink, bold=False, anchor='start'):
    d.add(String(x, y, text, fontName='Helvetica-Bold' if bold else 'Helvetica',
                 fontSize=size, fillColor=color, textAnchor=anchor))
if not args.manuscript:
    label(32, 511, 'Table VI as an interval plot', 18, bold=True)
    label(32, 490, 'Dots: mean score differences (first arm minus second). Bars: 95% intervals.', 10.5)
    label(32, 474, 'Positive favors the first arm. An interval spanning zero indicates no clear difference.', 10.5)
records = []
def fmt(v):
    return format(Decimal(str(v)).quantize(Decimal('.001'), rounding=ROUND_HALF_UP), '+.3f')

for k, (title, subtitle, path, contrasts, bounds, ticks) in enumerate(groups):
    if args.manuscript:
        header_y = [192, 192, 103, 46][k]
        short_titles = ['A  Completeness',
                        'B  FactualCorrectness',
                        'C  Lexical RR@10: direct, 105 FAQs',
                        'D  Lexical RR@10: O1, 111 FAQs']
        label([34, 157, 1, 1][k], header_y, short_titles[k], 8.3, bold=True)
        if k < 2:
            label([34, 157][k], header_y-12, ['1-5; 15 tasks', '0-1; 111 FAQs'][k], 7.8, gray)
        mean_x = 128 if k == 0 else 251
        label(mean_x, header_y-12 if k < 2 else header_y, 'Mean', 8, gray, anchor='end')
    else:
        base_x = 32 + (k % 2) * 370
        base_y = 280 if k < 2 else 91
        label(base_x, base_y + 160, title, 10.3, bold=True)
        label(base_x, base_y + 144, subtitle, 9.5, gray)
        label(base_x + 332, base_y + 144, 'Mean', 9, gray, anchor='end')
    obj = evidence
    for key in path:
        obj = obj[key]
    ys = (list(range(len(contrasts), 0, -1)) if args.manuscript else
          [4, 3, 2, 1] if len(contrasts) == 4 else ([3, 2] if len(contrasts) == 2 else [2.5]))
    rows = [obj[name] for name in contrasts]
    plot = LinePlot()
    if args.manuscript:
        plot.x = [34, 157, 35, 35][k]
        plot.y = [130, 130, 72, 27][k]
        plot.width = 62 if k < 2 else 174
        plot.height = 11*len(rows)
    else:
        plot.x, plot.y, plot.width, plot.height = base_x + 56, base_y + 35, 222, 99
    plot.data = [[(row['ci95'][0], y), (row['ci95'][1], y)] for row, y in zip(rows, ys)]
    plot.xValueAxis.valueMin, plot.xValueAxis.valueMax = bounds
    plot.xValueAxis.valueSteps = ([-.3, 0, .2] if k == 0 else [-.04, 0, .04]) if args.manuscript and k < 2 else ticks
    plot.xValueAxis.labels.fontName = 'Helvetica'
    plot.xValueAxis.labels.fontSize = 7.8 if args.manuscript else 8.5
    plot.xValueAxis.labelTextFormat = lambda v: '0' if abs(v) < 1e-10 else f'{v:+.2f}'
    plot.xValueAxis.strokeColor = light
    plot.yValueAxis.valueMin, plot.yValueAxis.valueMax = .5, len(rows)+.5 if args.manuscript else 4.5
    plot.yValueAxis.visible = False
    for j in range(len(rows)):
        plot.lines[j].strokeColor = blue
        plot.lines[j].strokeWidth = 1.7
    def px(value):
        return plot.x + (value - bounds[0]) / (bounds[1] - bounds[0]) * plot.width
    def py(value):
        return plot.y + (value - .5) / (len(rows) if args.manuscript else 4) * plot.height
    d.add(Line(px(0), plot.y, px(0), plot.y + plot.height,
               strokeColor=gray, strokeWidth=.8, strokeDashArray=[3, 3]))
    d.add(plot)
    for name, row, y in zip(contrasts, rows, ys):
        v = row['estimate']; lo, hi = row['ci95']
        assert lo <= v <= hi
        if not args.manuscript or k != 1:
            label(30 if args.manuscript else base_x + 48, py(y) - 2.8,
                  name.replace(' - ', '-'), 8 if args.manuscript else 9, anchor='end')
        label(mean_x if args.manuscript else base_x + 332, py(y) - 2.8,
              fmt(v), 8 if args.manuscript else 9, anchor='end')
        for endpoint in (lo, hi):
            cap = 2.2 if args.manuscript else 3
            d.add(Line(px(endpoint), py(y)-cap, px(endpoint), py(y)+cap, strokeColor=blue, strokeWidth=1.1))
        d.add(Circle(px(v), py(y), 2.3 if args.manuscript else 3.2, fillColor=blue, strokeColor=blue))
        records.append({'path':path+[name], 'estimate':v, 'ci95':[lo,hi],
                        'display':[fmt(v),fmt(lo),fmt(hi)]})
    if not args.manuscript:
        label(base_x + 167, base_y + 6, 'Mean score difference', 9, gray, anchor='middle')

if not args.manuscript:
    label(32, 66, 'Scales differ by metric; the two RR@10 panels share a scale. Values are score units, not percentages.', 9)
    label(32, 51, 'Intervals are unadjusted for multiple comparisons; they do not establish equivalence or practical importance.', 9)
    label(32, 36, 'Answer scores pool conditions. O1 changes support credit and the question set. S-O is replay, not a noise bound.', 9)
    label(32, 17, 'Preview only - not inserted in the manuscript.', 8.5, gray)

# Ensure the preview contains exactly the same displayed estimates and intervals.
table = (PAPER/'tables/w02-answers.tex').read_text()
triples = re.findall(r'& \$([+-]\d+\.\d+)\$ & \$\[([+-]\d+\.\d+),([+-]\d+\.\d+)\]\$', table)
assert triples == [tuple(row['display']) for row in records], (triples,records)
renderPDF.drawToFile(d, str(OUT), title='Answer and retrieval score differences', author='Rocker D\u2019Antonio')
manifest = {'source':source, 'rows':records, 'pdf_sha256':hashlib.sha256(OUT.read_bytes()).hexdigest(),
            'table_sha256':hashlib.sha256((PAPER/'tables/w02-answers.tex').read_bytes()).hexdigest(),
            'status':('Manuscript figure' if args.manuscript else 'Standalone preview, not included in manuscript') + '; no scientific recomputation',
            'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
OUT.with_suffix('.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8')
print(f'Preview: {OUT}; all {len(records)} rows match Table VI.')
