"""Redraw recorded refinement profiles with direct labels and a common scale."""
import csv
import hashlib
import json
from pathlib import Path
from reportlab.lib.colors import HexColor, white
from reportlab.pdfgen import canvas

PAPER = Path(__file__).resolve().parents[1]
ROOT = PAPER.parents[1]
SOURCE = ROOT / 'git/context/evidence/normalized-magnetic-22-v1-2026-10-25.json'
CSV = ROOT / 'artifacts/normalized-magnetic-22-v1/figures/fig-c-refinement.csv'
assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == '47bad4ea0a8132ef0e37b6a09a0627a2bdb5e139e35bf171f8097397b8431e3e'
profiles = json.loads(SOURCE.read_text())['C_refinement']['profiles']
records = list(csv.DictReader(CSV.open(newline='', encoding='utf-8')))
assert len(records) == 102
for row in records:
    suffix = row['graph']
    if suffix in ('LN', 'PC', 'RN'):
        suffix = suffix.lower() + '-' + row['seed_index']
    key = 'wc/' + row['arm'] + ('' if suffix == 'arm' else '/' + suffix)
    assert float(row['value']) == profiles[key][row['measure']]

out = PAPER / 'figures/w02-refinement.pdf'
c = canvas.Canvas(str(out), pagesize=(504, 184), invariant=1)
c.setTitle('Refined documentation and graph controls compared with the original')
c.setAuthor('')
ink = HexColor('#202C38')
muted = HexColor('#606D77')
grid = HexColor('#E5E9EC')
colors = {'LN': HexColor('#2775B6'), 'PC': HexColor('#16856A'), 'RN': HexColor('#A66A00')}
panels = [('lambda1_q0.25', 'First magnetic mode', 'q = 0.25'),
          ('lambda2_q0.25', 'Second magnetic mode', 'q = 0.25'),
          ('weighted_q0_lambda2', 'Undirected connectivity', 'second mode, q = 0')]
arm_rows = {'LT': [139, 126, 113, 100], 'R0': [70, 57, 44, 31]}
row_labels = [('arm', 'Refined corpus'), ('LN', 'Locality (LN)'), ('PC', 'Page creation (PC)'), ('RN', 'Random edits (RN)')]
offsets = [-2.4, -1.2, 0, 1.2, 2.4]
def label(x, y, text, size=8.3, bold=False, color=ink):
    c.setFillColor(color); c.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
    c.drawString(x, y, text)
def diamond(x,y):
    path=c.beginPath(); path.moveTo(x,y+3.2); path.lineTo(x+3.2,y);path.lineTo(x,y-3.2);path.lineTo(x-3.2,y);path.close()
    c.setFillColor(ink);c.setStrokeColor(white);c.setLineWidth(.55);c.drawPath(path,fill=1,stroke=1)
label(0,151,'Leaf-and-trunk (LT)',8.5,True)
label(0,82,'Uninformed (R0)',8.5,True)
for arm,ys in arm_rows.items():
    for (_, text),y in zip(row_labels,ys): label(3,y-2.7,text,8.2,text=='Refined corpus')
for j,(measure,title,subtitle) in enumerate(panels):
    left=103+j*135; width=113
    def xpos(value): return left+width*value/.20
    c.setFont('Helvetica-Bold',8.7);c.setFillColor(ink);c.drawCentredString(left+width/2,174,title)
    c.setFont('Helvetica',8.2);c.setFillColor(muted);c.drawCentredString(left+width/2,163,subtitle)
    for tick in (0,.05,.10,.15,.20):
        x=xpos(tick);c.setStrokeColor(grid);c.setLineWidth(.45);c.line(x,24,x,148)
        c.setFont('Helvetica',7.6);c.setFillColor(muted);c.drawCentredString(x,14,f'{tick:.2f}')
    baseline=profiles['wc/O'][measure]
    assert all(profiles[f'wc/{arm}/O-prime'][measure]==baseline for arm in arm_rows)
    c.setStrokeColor(muted);c.setDash(2,2);c.setLineWidth(.75);c.line(xpos(baseline),24,xpos(baseline),148);c.setDash()
    c.setStrokeColor(grid);c.line(left,91,left+width,91)
    for arm,ys in arm_rows.items():
        diamond(xpos(profiles['wc/'+arm][measure]),ys[0])
        for (family,_),y in zip(row_labels[1:],ys[1:]):
            vals=[profiles[f'wc/{arm}/{family.lower()}-{k}'][measure] for k in range(1,6)]
            assert all(0<=v<=.20 for v in vals)
            c.setStrokeColor(colors[family]);c.setLineWidth(.65);c.line(xpos(min(vals)),y,xpos(max(vals)),y)
            for value,dy in zip(vals,offsets):
                c.setFillColor(colors[family]);c.setStrokeColor(white);c.setLineWidth(.3)
                c.circle(xpos(value),y+dy,2.1,fill=1,stroke=1)
    assert 0<=baseline<=.20
c.setFont('Helvetica',8);c.setFillColor(muted)
c.drawCentredString(300,2,'Eigenvalue (same scale in all panels)')
c.showPage();c.save()
ledger={'purpose':'Presentation only; same 102 recorded arm/control/mapped-baseline values, plus three original baselines.',
        'source':str(SOURCE.relative_to(ROOT)), 'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'csv':str(CSV.relative_to(ROOT)), 'csv_sha256':hashlib.sha256(CSV.read_bytes()).hexdigest(),
        'records_checked':len(records), 'control_points_drawn':90, 'refined_points_drawn':6,
        'baseline':'Original O equals both mapped O-prime values exactly for every plotted measure; one dashed line per panel.',
        'encoding':'Directly labelled rows; common 0–0.20 horizontal scale; 5 controls per row; vertical offsets only distinguish points; range segment is min–max, not uncertainty.',
        'output_sha256':hashlib.sha256(out.read_bytes()).hexdigest()}
(PAPER/'figures/refinement-comparison-provenance.json').write_text(json.dumps(ledger,indent=2)+'\n',encoding='utf-8',newline='\n')
print('Redrew 90 controls, 6 refinements and 3 coincident baseline lines; checked 102 CSV values.')
