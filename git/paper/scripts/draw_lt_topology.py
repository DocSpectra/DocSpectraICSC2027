"""Draw the LT design hypothesis; illustrative roles, no empirical graph data."""
import math
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

PAPER=Path(__file__).resolve().parents[1]
out=PAPER/'figures/leaf-trunk-topology.pdf'
c=canvas.Canvas(str(out),pagesize=(252,238),invariant=1)
c.setTitle('Leaf-and-trunk topology for hypothesized search-led access')
c.setAuthor('')
ink=HexColor('#253649'); blue=HexColor('#195f91'); green=HexColor('#17644d')

def box(x,y,w,h,lines,color,fill):
    c.setStrokeColor(color);c.setFillColor(HexColor(fill));c.setLineWidth(.9)
    c.roundRect(x,y,w,h,5,stroke=1,fill=1)
    c.setFillColor(ink)
    for i,line in enumerate(lines):
        c.setFont('Helvetica-Bold' if i==0 else 'Helvetica',9 if i==0 else 8.5)
        c.drawCentredString(x+w/2,y+h/2+(len(lines)-1)*5.3-i*10.6-3,line)

def arrow(a,b,color,dashed=False):
    x1,y1=a;x2,y2=b;dx=x2-x1;dy=y2-y1;length=math.hypot(dx,dy);ux=dx/length;uy=dy/length
    c.setStrokeColor(color);c.setFillColor(color);c.setLineWidth(1.2)
    c.setDash(4,3) if dashed else c.setDash()
    c.line(x1,y1,x2,y2);c.setDash()
    p=c.beginPath();p.moveTo(x2,y2);p.lineTo(x2-6*ux+2.7*uy,y2-6*uy-2.7*ux)
    p.lineTo(x2-6*ux-2.7*uy,y2-6*uy+2.7*ux);p.close();c.drawPath(p,stroke=0,fill=1)

# Single-column layout at native 252-point width: retain readable 8.5--9 pt labels.
box(72,185,108,45,['Search entry','Query / symptom','Image pull fails'],ink,'#f3f5f7')
box(4,105,76,45,['Leaf','Push an image'],blue,'#eef5fb')
box(88,105,76,45,['Leaf','Pull a private','image'],blue,'#eef5fb')
box(172,105,76,45,['Leaf','Configure a','registry'],blue,'#eef5fb')
box(8,28,110,50,['Shared trunk','Registry authentication','Reusable steps'],green,'#eef7f3')
box(134,28,110,50,['Shared trunk','TLS / trust settings','Shared prerequisites'],green,'#eef7f3')

arrow((126,185),(126,150),ink,True)
arrow((42,105),(42,78),blue)
arrow((112,105),(91,78),blue)
arrow((140,105),(161,78),blue)
arrow((210,105),(210,78),blue)

# Legend distinguishes a retrieval transition from an authored hyperlink.
arrow((8,12),(27,12),ink,True)
c.setFillColor(ink);c.setFont('Helvetica',8.5);c.drawString(33,9,'search landing')
arrow((125,12),(144,12),blue)
c.setFillColor(ink);c.drawString(150,9,'authored hyperlink')
c.showPage();c.save()
print(out)
