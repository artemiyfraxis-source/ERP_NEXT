# pip install ezdxf pymupdf
"""Собирает комплект на жгут: DXF-листы и общий PDF в натуральную величину.

python build.py [--preview]  — --preview кладет PNG страниц в preview/
"""
import pathlib
import sys

import pymupdf
from ezdxf.addons.drawing import Frontend, RenderContext, config, layout
from ezdxf.addons.drawing import pymupdf as pdf_backend

from zhgut_e4 import e4_sheet1
from zhgut_sb import sb_sheet
from zhgut_spec import spec_sheets

A3, A4 = (420, 297), (210, 297)
SHEETS = [("zhgut_sb.dxf", sb_sheet, A3), ("zhgut_e4.dxf", e4_sheet1, A3), ("zhgut_spec.dxf", spec_sheets, A4)]
PDF = "zhgut_komplekt.pdf"


def pdf_page(doc, size):
    backend = pdf_backend.PyMuPdfBackend()
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE,
                               color_policy=config.ColorPolicy.COLOR,
                               lineweight_policy=config.LineweightPolicy.ABSOLUTE)
    Frontend(RenderContext(doc), backend, config=cfg).draw_layout(doc.modelspace())
    page = layout.Page(*size, layout.Units.mm, margins=layout.Margins.all(0))
    return backend.get_pdf_bytes(page, settings=layout.Settings(fit_page=False, scale=1, max_stroke_width=0.01))


book = pymupdf.open()
for name, make, size in SHEETS:
    docs = make()
    docs = docs if isinstance(docs, list) else [docs]
    for i, doc in enumerate(docs, 1):
        errors = doc.audit().errors
        assert not errors, f"{name}: ошибки аудита DXF {errors}"
        doc.saveas(name if len(docs) == 1 else name.replace(".dxf", f"_{i}.dxf"))
        book.insert_pdf(pymupdf.open("pdf", pdf_page(doc, size)))
book.save(PDF)
if "--preview" in sys.argv:
    pathlib.Path("preview").mkdir(exist_ok=True)
    for i, page in enumerate(book, 1):
        page.get_pixmap(dpi=110).save(f"preview/page{i}.png")
print(f"{PDF}: {len(book)} стр.; DXF: {', '.join(n for n, *_ in SHEETS)}")

# --out ПАПКА: комплект для выдачи — общий PDF и отдельные документы (ГОСТ 2.102: СБ, Э4 и спецификация — разные документы)
if "--out" in sys.argv:
    from harness_data import BASE, NAME
    out = pathlib.Path(sys.argv[sys.argv.index("--out") + 1])
    out.mkdir(parents=True, exist_ok=True)
    base = f"{BASE} {NAME}"
    book.save(out / f"{base} — СБ, Э4, спецификация.pdf")
    start = 0
    for (name, make, size), title in zip(SHEETS, ["СБ — сборочный чертёж", "Э4 — схема электрическая соединений", "спецификация"]):
        count = len(make()) if name == "zhgut_spec.dxf" else 1
        part = pymupdf.open()
        part.insert_pdf(book, from_page=start, to_page=start + count - 1)
        part.save(out / f"{base} {title}.pdf")
        start += count
    print(f"выдано в {out}: 4 PDF")
