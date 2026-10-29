"""Server-side SVG line charts: no JavaScript libraries needed."""
from html import escape


def line_chart(values, title, unit, markers=(), threshold=None, width=640, height=180):
    left, right, top_pad, bottom = 44, 12, 24, 22
    top = max(max(values), threshold or 0, 1) * 1.1
    count = len(values)

    def x(i):
        return left + (width - left - right) * i / max(1, count - 1)

    def y(v):
        return height - bottom - (height - top_pad - bottom) * v / top

    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="{escape(title)}">',
             f'<text x="{left}" y="14" class="ctitle">{escape(title)} ({escape(unit)})</text>',
             f'<line x1="{left}" y1="{y(0):.1f}" x2="{width - right}" y2="{y(0):.1f}" class="axisline"/>',
             f'<text x="4" y="{y(0) + 4:.0f}" class="axis">0</text>',
             f'<text x="4" y="{y(top / 1.1) + 4:.0f}" class="axis">{top / 1.1:.0f}</text>']
    if threshold:
        parts.append(f'<line x1="{left}" y1="{y(threshold):.1f}" x2="{width - right}" y2="{y(threshold):.1f}" class="thr"/>')
    for minute, label, kind in markers:
        parts.append(f'<line x1="{x(minute):.1f}" y1="{top_pad}" x2="{x(minute):.1f}" y2="{y(0):.1f}" class="mark {escape(kind)}"/>')
        parts.append(f'<text x="{x(minute) + 3:.1f}" y="{top_pad + 8}" class="marklabel {escape(kind)}">{escape(label)}</text>')
    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    parts.append(f'<polyline points="{points}" class="series"/></svg>')
    return "".join(parts)
