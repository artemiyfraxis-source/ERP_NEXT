"""Small server-rendered passport page; it has no application shell, scripts or external resources."""

from html import escape
import math

from .instrument_characterization import number
from .instrument_passport import FREQUENCY_LIMIT_GHZ, LOSS_SCALE, VSWR_SCALE, _envelope, band_rows
from .instrument_public import PublicMeasurement, PublicPassport, public_fields, public_measurement_fields


PUBLIC_STYLE = """
:root{font-family:Arial,sans-serif;color:#202124;background:#f5f7f8;color-scheme:light}
*{box-sizing:border-box}body{margin:0}main{max-width:900px;margin:auto;padding:24px 20px 40px}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:20px}
h1{font-size:26px;margin:4px 0 0}h2{font-size:19px;margin:0 0 16px}h3{font-size:15px;margin:20px 0 8px}
.brand{color:#555;font-size:13px;letter-spacing:.04em}.name{font-size:18px;font-weight:600;line-height:1.4;margin:0 0 20px}
section{background:white;border:1px solid #dadce0;border-radius:10px;padding:22px;margin-bottom:16px}
a.download{background:#008577;color:white;text-decoration:none;padding:12px 18px;border-radius:6px;white-space:nowrap;font-weight:600;min-height:44px}
a:focus-visible{outline:3px solid #202124;outline-offset:4px}dl{display:grid;grid-template-columns:minmax(130px,1fr) 2fr;gap:10px 18px;margin:0;line-height:1.45}
dt{color:#555}dd{margin:0;font-weight:500;overflow-wrap:anywhere}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
th,td{padding:10px 8px;border-bottom:1px solid #dadce0;text-align:right}th:first-child,td:first-child{text-align:left}th{font-weight:600;color:#555}
svg{display:block;width:100%;height:auto;font-variant-numeric:tabular-nums}svg text{font-family:Arial,sans-serif;font-size:12px;fill:#444}.chart-narrow{display:none}.chart-narrow text{font-size:14px}
.empty{padding:8px 0;color:#555}.note{font-size:13px;color:#555;line-height:1.5}.legend{display:flex;gap:20px;font-size:13px}.side-one{color:#1950be}.side-two{color:#be2d23}
footer{color:#666;font-size:12px;text-align:center;margin-top:24px}@media(max-width:600px){main{padding:18px 12px 28px}header{align-items:flex-start;flex-wrap:wrap}h1{font-size:24px}section{padding:16px}dl{grid-template-columns:1fr;gap:3px}dd{margin-bottom:10px}th,td{padding:9px 4px;font-size:12px}.name{font-size:17px}a.download{font-size:14px}.chart-wide{display:none}.chart-narrow{display:block}}
""".strip()


def _fields(rows) -> str:
    return "<dl>" + "".join(f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in rows) + "</dl>"


def _svg(measured: PublicMeasurement, reflection: bool, *, compact=False) -> str:
    minimum, maximum, step = VSWR_SCALE if reflection else LOSS_SCALE
    stop = min(FREQUENCY_LIMIT_GHZ, measured.band[1]) or FREQUENCY_LIMIT_GHZ
    title = "КСВН" if reflection else "Передача S21, дБ"
    left, top, width, height = (34, 12, 300, 250) if compact else (44, 12, 640, 220)
    viewbox = "0 0 350 310" if compact else "0 0 710 270"
    variant = "chart-narrow" if compact else "chart-wide"
    body = [f'<div class="{variant}"><svg viewBox="{viewbox}" role="img" aria-label="{title}"><title>{title}</title>']
    for index in range(11):
        y = top + index * height / 10
        label = number(round(maximum - index * step, 1))
        body.append(f'<line x1="{left}" y1="{y:g}" x2="{left + width}" y2="{y:g}" stroke="#ddd"/>')
        body.append(f'<text x="{left - 8}" y="{y + 4:g}" text-anchor="end">{label}</text>')
    for index in range(7):
        x = left + index * width / 6
        label = number(round(stop * index / 6, 2))
        body.append(f'<line x1="{x:g}" y1="{top}" x2="{x:g}" y2="{top + height}" stroke="#ddd"/>')
        body.append(f'<text x="{x:g}" y="{top + height + 19}" text-anchor="middle">{label}</text>')
    body.append(f'<text x="{left + width}" y="{307 if compact else 267}" text-anchor="end">ГГц</text>')
    clipped = False
    for name, color in zip(("S11", "S22") if reflection else ("S21",), ("#1950be", "#be2d23")):
        points = measured.points(name)
        if not reflection:
            points = tuple((f, 20 * math.log10(v) if v > 0 else -math.inf) for f, v in points)
        clipped |= any(v < minimum or v > maximum for _, v in points)
        coords = " ".join(f"{left + f / (stop * 1e9) * width:.2f},{top + (maximum - min(maximum, max(minimum, v))) / (maximum - minimum) * height:.2f}"
                          for f, v in _envelope(points, stop))
        body.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="1.8"/>')
    body.append("</svg>")
    if clipped:
        body.append('<p class="note">Значения вне шкалы показаны на её границе; максимумы — в таблице.</p>')
    body.append("</div>")
    return "".join(body)


def public_passport_html(passport: PublicPassport, *, pdf_path: str) -> str:
    title = f"Паспорт {passport.inventory_number}"
    parts = ['<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer">',
             f'<title>{escape(title)}</title><style>{PUBLIC_STYLE}</style></head><body><main><header>',
             f'<div><span class="brand">FRAXIS ERP</span><h1>{escape(title)}</h1></div>',
             f'<a class="download" href="{escape(pdf_path, quote=True)}" download="passport.pdf">Скачать PDF</a></header>',
             f'<section aria-label="Сведения об изделии"><p class="name">{escape(passport.name)}</p>',
             _fields(public_fields(passport))]
    if passport.impedance_warning:
        parts.append(f'<p><strong>{escape(passport.impedance_warning)}</strong></p>')
    parts.append('</section><section aria-labelledby="measurement"><h2 id="measurement">Результаты замера</h2>')
    measured = passport.measurement
    if measured is None:
        parts.append('<p class="empty">Замер не проведён</p>')
    else:
        parts += [_fields(public_measurement_fields(measured)), '<h3>Параметры по диапазонам</h3><div class="table-wrap"><table><thead><tr>',
                  '<th scope="col">Диапазон, ГГц</th><th scope="col">КСВН 1, макс.</th><th scope="col">КСВН 2, макс.</th><th scope="col">Потери, дБ</th></tr></thead><tbody>']
        for row in band_rows(measured):
            parts.append("<tr>" + "".join(f"<td>{escape(value)}</td>" for value in row) + "</tr>")
        parts += ['</tbody></table></div><h3>КСВН</h3><div class="legend"><span class="side-one">Сторона 1</span><span class="side-two">Сторона 2</span></div>',
                  _svg(measured, True), _svg(measured, True, compact=True), '<h3>Передача S21, дБ</h3>',
                  _svg(measured, False), _svg(measured, False, compact=True)]
    parts.append('</section><footer>Fraxis ERP</footer></main></body></html>')
    return "".join(parts)
