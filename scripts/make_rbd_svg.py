# generates docs/rbd.svg - reliability block diagram, baseline vs. proposed
# Run from the repository root:  python3 scripts/make_rbd_svg.py
W, H = 1000, 440
out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif">',
       f'<rect width="{W}" height="{H}" fill="#fff"/>',
       '<text x="500" y="28" text-anchor="middle" style="font-size:18px;font-weight:bold">Reliability block diagram (series of parallel subsystems)</text>']
def block(x, y, w, h, label, sub, cls):
    fill, stroke = {"base": ("#fff", "#4a6fa5"), "new": ("#fff6e5", "#d97706"), "weak": ("#fdecea", "#c62828")}[cls]
    dash = ' stroke-dasharray="6 4"' if cls == "new" else ""
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1.8"{dash}/>')
    out.append(f'<text x="{x+w/2}" y="{y+h/2-2}" text-anchor="middle" style="font-size:12.5px;font-weight:bold">{label}</text>')
    out.append(f'<text x="{x+w/2}" y="{y+h/2+13}" text-anchor="middle" style="font-size:11px;fill:#444">{sub}</text>')
def line(x1, y1, x2, y2):
    out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#444" stroke-width="1.5"/>')
def row(y0, title, lb2, gw2, total, ok):
    out.append(f'<text x="30" y="{y0}" style="font-size:14px;font-weight:bold">{title}</text>')
    cy = y0 + 85
    tiers = [("LB", "99.95%", lb2, "99.99998%"), ("Gateway", "99.90%", gw2, "99.9999%"),
             ("Auth", "99.5%", True, "99.9975%"), ("Course", "99.5%", True, "99.9975%"),
             ("Registration", "99.5%", True, "99.9975%"), ("DB node", "99.7%", True, "99.9991%")]
    x = 62; bw, bh, gap = 100, 40, 30
    out.append(f'<text x="30" y="{cy+5}" style="font-size:12px">IN</text>'); line(50, cy, x, cy)
    for name, r, par, rp in tiers:
        if par:
            line(x, cy, x, cy - 30); line(x, cy, x, cy + 30)
            line(x + bw + 14, cy - 30, x + bw + 14, cy + 30)
            line(x, cy - 30, x + 7, cy - 30); line(x + 7 + bw, cy - 30, x + bw + 14, cy - 30)
            line(x, cy + 30, x + 7, cy + 30); line(x + 7 + bw, cy + 30, x + bw + 14, cy + 30)
            new = name in ("LB", "Gateway")
            block(x + 7, cy - 30 - bh/2, bw, bh, name + " #1", r, "base")
            block(x + 7, cy + 30 - bh/2, bw, bh, name + " #2", r, "new" if new else "base")
            out.append(f'<text x="{x + 7 + bw/2}" y="{cy + 75}" text-anchor="middle" style="font-size:12px;fill:#1b5e20;font-weight:bold">{rp}</text>')
            xe = x + bw + 14
        else:
            block(x, cy - bh/2, bw + 14, bh, name, r, "weak")
            out.append(f'<text x="{x + (bw+14)/2}" y="{cy + 75}" text-anchor="middle" style="font-size:12px;fill:#c62828;font-weight:bold">{r} (SPOF)</text>')
            xe = x + bw + 14
        if name != "DB node":
            line(xe, cy, xe + gap, cy)
        x = xe + gap
    line(x - gap, cy, x - gap + 22, cy)
    out.append(f'<text x="{x - gap + 26}" y="{cy+5}" style="font-size:12px">OUT</text>')
    color = "#1b5e20" if ok else "#c62828"
    out.append(f'<text x="970" y="{y0}" text-anchor="end" style="font-size:14px;font-weight:bold;fill:{color}">{total}</text>')
row(62, "A) Baseline (as given): single LB and single Gateway", False, False, "End-to-end 99.842% — below 99.9%", False)
row(262, "B) Proposed: 2 API Gateways (+ 2 LBs recommended)", True, True, "99.942% (2 GW) · 99.991% (2 GW + 2 LB)", True)
out.append('</svg>')
open("docs/rbd.svg", "w").write("\n".join(out))
