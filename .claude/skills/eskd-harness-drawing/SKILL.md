---
name: eskd-harness-drawing
description: Make an ESKD drawing set for a cable harness / cable assembly — сборочный чертёж (СБ, A3), схема электрическая соединений (Э4, A3), спецификация (A4) — as DXF + PDF with ezdxf, from ERP data or the owner's cut list. Use when the owner asks «сделай чертёж», «чертёж жгута», «чертёж по ЕСКД», «СБ / Э4 / спецификация», «поправь чертёж», «перевыпусти чертёж», or sends a harness sketch to turn into documents.
---

# ESKD harness drawing set (СБ + Э4 + спецификация)

Reference implementation: `scripts/` — the set approved by the owner and Vasily Kostromin for 713К.4 КБ1 «Жгут НВ4»
(ЮТНВ.713К.4КБ1, 17.10.2026). Copy it to a work folder (scratchpad), change the data, adapt the layout, build, check, deliver.

| File | What |
|---|---|
| `harness_data.py` | THE single data source: designation, name, connectors, wires, splices, tubes, braid, marking, tolerances, deviations |
| `eskd.py` | sheet, frame, side fields 19–23, title blocks form 1 / 2 / 2а (ГОСТ 2.104), tables, dimensions, layers and line weights |
| `zhgut_sb.py` | СБ, A3, no scale: branches, connectors, braid, ТТК, marking places, splice point, dimensions, tables 1–2, TT |
| `zhgut_e4.py` | Э4, A3: connector tables, coloured wires, splice dots, wire numbers, перечень элементов, TT |
| `zhgut_spec.py` | спецификация, A4, form 1 (+1а), first sheet title form 2, next 2а |
| `build.py` | builds DXF + combined PDF; `--preview` → PNG per page; `--out DIR` → 4 PDFs with proper names |

## Setup (once per machine)
`python -m venv %USERPROFILE%\.claude\venvs\eskd` → `...\eskd\Scripts\pip install ezdxf==1.4.4 pymupdf==1.28.2 pillow==12.3.0 matplotlib==3.11.2` (ezdxf drawing add-on needs Pillow).
Run with `-X utf8`: `...\eskd\Scripts\python -X utf8 build.py --preview --out "%USERPROFILE%\Downloads"`.
Fonts: text style GOST = `isocpeur.ttf`; PDF text is drawn as outlines (no embedded font) — files are ~1,2 MB and an
outside reviewer's OCR may misread glued numbers («10,5 мм²» = pin «1» + «0,5 мм²»). Check the drawing, not the OCR.

## Procedure
1. **Data.** From ERP (project → КБ): BOM `GET /api/techcards/kitsets/{project}/bom/{КБ}`, the techcard
   `GET /api/techcards/projects/{card_id}` (`document.build_config.gen_state.S`: wires, endpoints/pins, splices, tails,
   twist, tubes, braid, marking rows), the owner's cut list and scheme. Ask the owner only for what is missing: lengths
   (drawing lengths are AFTER twisting), marking texts and distance, tube lengths, braid, splice method, deviations,
   stamp names. Never invent a length, a mass or a signature.
2. **Fill `harness_data.py`**; recompute derived values (wire kinds and metres, tube metres, braid metres, spec positions).
3. **Adapt the layout.** `zhgut_sb.py` / `zhgut_e4.py` geometry is laid out by hand for the 713К.4 topology (4 branches
   on side A → junction А → trunk → junction Б with splices → X5, X6 and a free-ends tail). Re-place branches, connectors,
   leaders, dimensions and E4 tables for the new harness; keep the rules below.
4. **Build** with `--preview`; **look at every page** (Read the PNGs) and at high-dpi crops of the title blocks, tables and
   TT (PyMuPDF `get_pixmap(dpi=130, clip=…)`). Nothing may touch or cross a line: TT vs title block, TT vs tables,
   header words vs cell borders, leaders vs text. A longer designation/name lengthens lines — re-check after every change.
5. **Deliver.** `build.py --out "%USERPROFILE%\Downloads"` → «{BASE} {NAME} — СБ, Э4, спецификация.pdf» plus separate
   «… СБ — сборочный чертёж.pdf», «… Э4 — схема электрическая соединений.pdf», «… спецификация.pdf». The project folder
   is the ERP kitset `folder_url` (Google Drive): without Google Drive for desktop the owner drags the files in — open the
   folder and select the file in Explorer for him. Keep the scripts of the job in the scratchpad until the owner is done.
6. **Keep ERP in step.** Lengths, tube lengths, braid pieces, marking distance and twist pitch on the drawing must equal the
   generator form of the techcard (ask before rebuilding a card someone else is editing; base edits on the card's current
   `gen_state`).

## Rules (owner + Vasily, 713К.4, 16–17.10)
- **Designation**: «ЮТНВ.» + internal number + document code: `ЮТНВ.713К.4КБ1 СБ`, `… Э4`, spec `ЮТНВ.713К.4КБ1`.
  Name = product name («Жгут НВ4»), second line = document name. Developer «Васильев», organisation «Fraxis».
- **Title block**: Масштаб «—» on СБ and Э4 (no scale). Лит., Масса, Пров., Т.контр., Н.контр., Утв. — ask the owner
  (литера for a one-off product is usually «И», ГОСТ 2.103); leave empty rather than invent.
- **Designators**: connectors X1…Xn, side A first, then side B; splices С1…Сn; marking places М1…Мn. Marking text = the
  board/address name («5», «I3», «C», «A6»); side-B connectors to the board are not marked.
- **СБ**: no colours (colours live on Э4). One line per branch (wires merged); braided branches thick; braid named by
  leaders («Оплетка XP-3»), ТТК over connector bodies by leaders with count («Трубка ТТК 8/2, 3 места»), trunk tube by
  leader. Dimensions: side A from the connector front face, side B to the rear face, free ends to the wire ends;
  tolerance in TT («+10/−5 мм»). Table 1 «Сращивания (в разветвлении Б)»: Индекс | Провод (поз. спец.) → второй конец |
  Способ | Изоляция (ТТК, L). Table 2 «Маркировка»: Индекс | Текст | От корпуса, мм | Способ | Материал. Index column
  ≥ 18 mm; long headers in two lines.
- **СБ TT** (in order): lengths and tolerance; electrical per Э4; splicing per table 1 + twisted pairs and pitch
  («свить попарно, шаг свивки 20 мм») + footnote of allowed methods (пайка, ленточный обжим, ультразвуковая сварка);
  free ends strip «2–3 мм» and tin; agreed deviations verbatim («— согласованное отклонение»); braid + ТТК with length
  («L = 15 мм»); marking per table 2.
- **Э4** (ERP look): connector tables Цвет | Сечение | Марка | Терминал | Цепь | № (side B mirrored), housing under the table,
  wires in their colours, wire numbers at the ends, splices as filled dots, twisted pairs drawn twisted; перечень элементов
  with an empty Примечание. TT: splices → СБ table 1; free ends «проводов № … (номера проводов по схеме)»; lengths → СБ;
  deviations; unused pins.
- **Spec**: Документация (СБ, Э4) → Прочие изделия (connectors grouped by type, contacts with counts, sleeves) →
  Материалы (wires per mark+colour in metres, tubes in metres, braid) → marking sleeves. The СБ tables reference these
  positions — renumbering the spec means re-checking table 1.
- **Drawing dimensions ≠ ERP wire lengths.** The СБ gives ASSEMBLY dimensions (connector front face → junction, junction →
  junction). The ERP generator form holds wire lengths (after twisting) and the techcard cut adds the twist allowance, the
  wire inside the contact/crimp, strip and splice overlap. 713К.4 (owner 18.10, «чертёж не менять»): СБ branch 120 + trunk
  А–Б 30 stay, while ERP form says 130 (X1/X2) / 137 (X3) and cuts 130–140. Never rescale СБ dimensions from ERP lengths
  unless the owner gives new assembly dimensions. Only tube lengths, braid piece lengths (spec metres), marking distance and twist pitch go 1:1 into the set.
- **Agreed deviations** are the owner's call (713К.4: НВ4 0,5 in SYM-001T-P0.6) — state them in TT, never «fix» them.
- **3D connector pictures** are not used on drawings; ETK/ERP keep their own sketches.
