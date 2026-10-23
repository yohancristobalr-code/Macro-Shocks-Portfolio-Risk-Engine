"""
research/charts.py

Dependency-free SVG line charts for the HTML report: responsive (viewBox),
readable in light and dark themes (axes use currentColor), one colour per
series with a legend, optional zero line and shaded bands.
"""

from __future__ import annotations

from html import escape
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

PALETTE = ("#2563eb", "#d97706", "#059669", "#dc2626", "#7c3aed", "#0891b2")
W, H = 640, 240
PAD_L, PAD_R, PAD_T, PAD_B = 52, 14, 30, 28


def _ticks(lo: float, hi: float, n: int = 4) -> List[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** np.floor(np.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=raw)
    start = np.ceil(lo / step) * step
    return [float(v) for v in np.arange(start, hi + step * 1e-9, step)]


def _fmt(v: float) -> str:
    a = abs(v)
    if a >= 10000:
        return f"{v / 1000:,.0f}k"
    if a >= 100:
        return f"{v:,.0f}"
    if a >= 10:
        return f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}"
    return f"{v:.2f}".rstrip("0").rstrip(".") if v else "0"


def line_chart(series: Dict[str, pd.Series], title: str, unit: str = "", zero: bool = False,
               bands: Optional[Sequence[Tuple[pd.Timestamp, pd.Timestamp, str]]] = None,
               note: str = "") -> str:
    """One SVG chart; ``series`` maps a legend label to a dated series."""
    clean = {k: s.dropna() for k, s in series.items() if s is not None and len(s.dropna()) > 1}
    if not clean:
        return f'<figure class="chart"><figcaption>{escape(title)}</figcaption><p class="muted">Sin datos.</p></figure>'
    x0 = min(s.index[0] for s in clean.values())
    x1 = max(s.index[-1] for s in clean.values())
    lo = min(float(s.min()) for s in clean.values())
    hi = max(float(s.max()) for s in clean.values())
    if zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    span = (hi - lo) or 1.0
    lo, hi = lo - span * 0.05, hi + span * 0.05
    t0, t1 = pd.Timestamp(x0).value, pd.Timestamp(x1).value
    sx = lambda t: PAD_L + (pd.Timestamp(t).value - t0) / max(t1 - t0, 1) * (W - PAD_L - PAD_R)
    sy = lambda v: PAD_T + (hi - v) / (hi - lo) * (H - PAD_T - PAD_B)

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{escape(title)}">']
    for a, b, label in bands or []:
        xa, xb = sx(max(pd.Timestamp(a), pd.Timestamp(x0))), sx(min(pd.Timestamp(b), pd.Timestamp(x1)))
        if xb > xa:
            out.append(f'<rect x="{xa:.1f}" y="{PAD_T}" width="{xb - xa:.1f}" height="{H - PAD_T - PAD_B}" '
                       f'class="band"><title>{escape(label)}</title></rect>')
    for v in _ticks(lo, hi):
        y = sy(v)
        out.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{PAD_L - 6}" y="{y + 4:.1f}" class="tick" text-anchor="end">{_fmt(v)}</text>')
    if zero and lo < 0 < hi:
        out.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{sy(0):.1f}" y2="{sy(0):.1f}" class="zero"/>')
    years = pd.date_range(pd.Timestamp(x0), pd.Timestamp(x1), freq="YS")
    if len(years) > 8:
        years = years[:: int(np.ceil(len(years) / 8))]
    if len(years) < 2:
        years = pd.date_range(pd.Timestamp(x0), pd.Timestamp(x1), periods=4)
    for t in years:
        label = t.strftime("%Y") if len(years) > 2 and t.month == 1 and t.day == 1 else t.strftime("%b %y")
        out.append(f'<text x="{sx(t):.1f}" y="{H - 8}" class="tick" text-anchor="middle">{label}</text>')
    lx, ly = PAD_L, 8
    for i, (name, s) in enumerate(clean.items()):
        color = PALETTE[i % len(PALETTE)]
        step = max(1, len(s) // 400)                       # keep files small
        pts = s.iloc[::step]
        if pts.index[-1] != s.index[-1]:
            pts = pd.concat([pts, s.iloc[-1:]])
        d = " ".join(f"{sx(t):.1f},{sy(float(v)):.1f}" for t, v in pts.items())
        out.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="1.8" '
                   f'stroke-linejoin="round"><title>{escape(name)}</title></polyline>')
        text = f"{name}: {_fmt(float(s.iloc[-1]))}{unit}"
        width = 22 + 6.2 * len(text)                       # approximate text width at 11px
        if lx + width > W - PAD_R and lx > PAD_L:           # wrap the legend to a second row
            lx, ly = PAD_L, ly + 13
        out.append(f'<rect x="{lx}" y="{ly}" width="10" height="10" rx="2" fill="{color}"/>'
                   f'<text x="{lx + 14}" y="{ly + 9}" class="legend">{escape(text)}</text>')
        lx += width
    out.append("</svg>")
    caption = f"<figcaption>{escape(title)}" + (f' <span class="muted">· {escape(note)}</span>' if note else "") + "</figcaption>"
    return f'<figure class="chart">{caption}{"".join(out)}</figure>'
