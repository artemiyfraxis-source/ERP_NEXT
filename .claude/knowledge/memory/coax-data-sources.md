---
name: coax-data-sources
description: "Owner's three main coax suppliers (АМЭЛ, Kroks, gsm-repiteri) and reference sources for coax cable/connector data and supplier articles — gsm-repiteri.ru, Амел, kroks.ru, vegatel.ru; resellers are suppliers, not makers"
metadata:
  node_type: memory
  type: project
  originSessionId: 18c9080e-85fd-4574-a846-0e4e1270987e
  modified: 2026-10-14T14:35:04.898Z
---

Owner 14.10.2026: coax data (cables, connectors, specs, supplier articles) is taken from the suppliers' sites —
gsm-repiteri.ru, «Амел», kroks.ru, vegatel.ru («оттуда кстати и артикулы»).

- A reseller is a «Поставщик», never a «Производитель» (owner: «больше надо не производитель, а поставщик, но
  производителя тоже можно оставить»). On 14.10 the reseller text of 17 coax cables was moved to «Поставщик» (domain),
  the old text kept in «Источник данных».
- Connectors without a maker article must not carry the site's number in the name («а зачем мне номера?») — they are
  named by type / angle / mount / cable (PR #412).

- 15.10.2026 the owner named the THREE main coax-cable suppliers — АМЭЛ (amel.ru: жёсткие / полужёсткие / гибкие),
  Kroks (kroks.ru/shop/cable-rf) and gsm-repiteri.ru (cable, cable-75-om, cable-vegatel) — and said «всё остальное
  можно сносить». The coax-cable catalogue was reloaded from them (PR db-amel-coax-1015, 141 rows); cables outside the
  three were deleted unless a БД.КОАКС connector, a stock lot or a kit references them (15 kept). Deleted rows are
  in docs/data «Коакс кабели — удалено 15.10.csv».

- radiolab.ru is a datasheet source for connector interface dimensions (owner 19.10): per-series PDFs `radiolab.ru/ru/attach_file/<series>radiolab.pdf` (sma, n, bnc, tnc, uhf, mcx, mmcx, smb…), curl works, page 1 = «Габаритно-присоединительные размеры» (мм/inch tables; keep mm only). «Заглубление контакта» = body face → contact, read by tracing extension lines (SMA male E; female H+I+J).

- MIL-STD-348A (interface figures 301-2 BNC, 304-2 N, 311 SMB, 313 TNC; free, scanned): `curl -L` from Web Archive works from here —
  `https://web.archive.org/web/20260521052714id_/https://everyspec.com/MIL-STD/MIL-STD-0300-0499/download.php?spec=MIL-STD-348A.029180.pdf`
  (everyspec itself 403; `web.archive.org/cdx/search/cdx?url=everyspec.com/...&matchType=prefix` finds snapshots). Figures are
  images — render pages (PyMuPDF) and read the lines. No MCX/MMCX there (CECC). ГОСТ 13317-89 covers only precision air-line 7/3,5/2,4 mm — no N/TNC.
- MIL-STD-348A confirms (19.10): N socket F−E = 9,04…9,19 − 4,75…5,26; TNC and BNC socket G−J = 8,31…8,51 − 4,72…5,23. Plugs: nut front is not an
  interface dimension (TNC pin fig 313-1: nut M ≤ 1,98 mm ahead of REF, pin tip recessed F ≥ 0,15), SMB pin interface gives only depth ≥ 0,36.

- MIL-STD-348B (2014; SMB figs 311-1 pin / 311-2 socket with full min…max; no MCX/MMCX/UHF/FME in it): `curl -L -A "Mozilla/5.0" "https://web.archive.org/web/2026id_/https://everyspec.com/MIL-STD/MIL-STD-0300-0499/download.php?spec=MIL-STD-348B.051541.pdf"` (3,4 MB, 222 pages, fig 311 on pp. 79–81).
- Amphenol Connex catalogs, interface dimensions in mm: MCX `https://web.archive.org/web/2024id_/https://www.peigenesis.com/images/content/pei_tabs/amphenol/rf-series/connex/mcx.pdf` (p. 2 «Interface mating dimensions», plug and jack, full ranges); MMCX same path `…/connex/mmcx.pdf` (p. 2; jack face→REF only «2,60 min»); FME `…/connex/fme.pdf` (no interface page). peigenesis direct = 403 for curl, use the Web Archive `2024id_` form.
- TE catalog 1307191 MMCX (Web Archive `2024id_` of `te.com/commerce/DocumentDelivery/DDEController?Action=srchrtrv&DocNm=1307191_MMCX_Connectors&DocType=CS&DocLang=English&DocFilename=ENG_CS_1307191_MMCX_Connectors_0307.pdf`, p. 2): MMCX plug/jack drawing with mm — same one-sided numbers as Amphenol (jack face→REF 2,6 min, contact 1,05 max).
- Amphenol RF FME interface drawing (image only, letters A–G, no table; the table is radiolab's): `https://web.archive.org/web/2018id_/https://www.amphenolrf.com/media/wysiwyg/FME_Interface_Drawing.jpg`.
- Dead ends 19.10: mouser/farnell/te/octopart/times direct links 403 or HTML; Amphenol UHF catalog (farnell 1663862, Web Archive ok) has no interface drawing; IEC 61169-10/-36/-52 and CECC 22220 are paid.

**How to apply:** coax cables come only from АМЭЛ / Kroks / gsm-repiteri; a new cable from elsewhere is an exception
to flag, not to add silently. Enrich from these first; fill only empty cells, write to prod per [[prod-reference-write]].
[[module-reference-db]]
