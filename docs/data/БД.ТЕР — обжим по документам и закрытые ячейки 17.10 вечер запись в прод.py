"""Запись в прод 17.10.2026 (вечер) — БД.ТЕР: высоты обжима по документам производителя и «пустые» ячейки, закрытые причиной.
Журнал и откат — «БД.ТЕР — обжим по документам и закрытые ячейки 17.10 вечер.md / .csv».

1. Заполнено по документам TE (Web Archive, документ и ссылка — в «Источник высоты обжима»):
   - 638652-1, 638652-2 (AMP 1.5/2.8) — TE 114-13091 Rev B, Figure 2: высота по жиле в пустые ячейки; высота по изоляции и
     отрыв — значения производителя вместо прежних «по правилу Ø×1,2» и «по стандарту IEC» (старые — в журнале).
   - 282308-1 (Superseal 1.5) — TE 114-20058 Rev B1, Figure 4: 15 AWG дописан, 20/18/16 AWG — значения TE вместо «по аналогу»
     серии; по изоляции 3,4 (макс. по TE) вместо 3,75 / 4,00.
2. Высоты обжима нет по конструкции — причина в «Источник высоты обжима», ячейка высоты остаётся пустой:
   HARTING 09 67 000 8676 (точёный контакт, обжим вдавливанием), Molex 19003-0129 / -0135 (InsulKrimp, контроль калибром).
3. Пустые без документа — к источнику дописано, что проверено 17.10 и почему пусто (JAE FI-R/FI-S, Hirose DF20 3032,
   TE Mini-UMNL 16 AWG, Commercial MNL 163303/163555, FASTON 60294-1).

Путь — `_edit_in` со сверкой старого значения каждой ячейки (пустая — old ""; поменяли после снимка — отказ, не пишется).
Строка — по ID из снимка прода 17.10 (сверяется и артикул).
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  ... python - apply < этот файл
  ... python - dry   < этот файл   → всё «уже так»
Выполнено 17.10.2026: dry (16 проверено, отказов 0) → бэкап /backups/daily/erp_2026-10-17_160947.dump (5,5 МБ, sha256
2fa6ab74…f4b0b25f4, 16:09:47) → apply (16 записано) → dry (16 «уже так»).
"""
import json
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, StaleRecordError
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 17.10.2026", department="System")
REASON = "БД.ТЕР 17.10.2026 вечер: обжим по документам производителя (TE 114-13091, 114-20058), закрытые ячейки с причиной"
ROWS = json.loads(r'''[
 {
  "id": "3d94cdd1-d11f-4106-86f4-188f93606c7c",
  "article": "638652-1",
  "edits": {
   "Высота обжима проводника , мм": [
    "",
    "16 AWG: 1,31–1,41; 14 AWG: 1,56–1,66"
   ],
   "Высота обжима изоляции, мм": [
    "16 AWG: 3,00; 14 AWG: 4,55",
    "16 AWG: 2,15–2,35; 14 AWG: 2,50–2,70"
   ],
   "Усилие обрыва контакта, N": [
    "16 AWG: 135; 14 AWG: 200",
    "16 AWG: 150; 14 AWG: 220"
   ],
   "Источник высоты обжима": [
    "te.com product-638652-1.html (same family page as 638652-2 already in DB, \"AMP 1.5/2.8 CONNECTOR SYSTEM, RECPT AND TAB\") + DigiKey/TTI listings (\"638652-1 — Socket Contact, Tin, 14-16 AWG\", vs 638652-2 = 18-20 AWG already in DB) — sibling AWG-range receptacle, same gender/series. TAB (male) counterpart part number for this system not located in distributor search — series expansion incomplete on that side, решено агентом — проверить",
    "TE 114-13091 Rev B (1.5 mm Contact System), Figure 2, номер-представитель 638652: высота по жиле — 0,50 мм² / 20 AWG 0,98–1,08; 0,75 / 18 AWG 1,08–1,18; 1,0 мм² 1,11–1,21; 1,3 / 16 AWG 1,31–1,41; 2,0 / 14 AWG 1,56–1,66; по изоляции («O»-обжим, ±0,10) 1,80 / 2,00 / 2,25 / 2,60; отрыв 85 / 110 / 150 / 220 Н. Даташит TE 638652-x ссылается на 114-13091 Rev B. https://web.archive.org/web/20230502065839id_/http://www.te.com/commerce/DocumentDelivery/DDEController?Action=showdoc&DocId=Specification+Or+Standard%7F114-13091%7FB%7Fpdf%7FEnglish%7FENG_SS_114-13091_B.pdf%7F638652-1"
   ]
  }
 },
 {
  "id": "f9eee613-7475-4935-adf0-1db900cc7a54",
  "article": "638652-2",
  "edits": {
   "Высота обжима проводника , мм": [
    "",
    "20 AWG: 0,98–1,08; 18 AWG: 1,08–1,18"
   ],
   "Высота обжима изоляции, мм": [
    "20 AWG: 2,60; 18 AWG: 3,80",
    "20 AWG: 1,70–1,90; 18 AWG: 1,90–2,10"
   ],
   "Усилие обрыва контакта, N": [
    "20 AWG: 60; 18 AWG: 90",
    "20 AWG: 85; 18 AWG: 110"
   ],
   "Источник высоты обжима": [
    "нет данных производителя: TE AMP 1.5/2.8 Connector System — application specification не найдена в открытом доступе",
    "TE 114-13091 Rev B (1.5 mm Contact System), Figure 2, номер-представитель 638652: высота по жиле — 0,50 мм² / 20 AWG 0,98–1,08; 0,75 / 18 AWG 1,08–1,18; 1,0 мм² 1,11–1,21; 1,3 / 16 AWG 1,31–1,41; 2,0 / 14 AWG 1,56–1,66; по изоляции («O»-обжим, ±0,10) 1,80 / 2,00 / 2,25 / 2,60; отрыв 85 / 110 / 150 / 220 Н. Даташит TE 638652-x ссылается на 114-13091 Rev B. https://web.archive.org/web/20230502065839id_/http://www.te.com/commerce/DocumentDelivery/DDEController?Action=showdoc&DocId=Specification+Or+Standard%7F114-13091%7FB%7Fpdf%7FEnglish%7FENG_SS_114-13091_B.pdf%7F638652-1"
   ]
  }
 },
 {
  "id": "4f416bd0-738b-4d47-946a-d53a1bbd34c3",
  "article": "282308-1",
  "edits": {
   "Высота обжима проводника , мм": [
    "20 AWG: 1,04–1,14; 18 AWG: 1,09–1,19; 16 AWG: 1,35–1,45",
    "20 AWG: 1,27–1,37; 18 AWG: 1,35–1,45; 16 AWG: 1,61–1,71; 15 AWG: 1,61–1,71"
   ],
   "Высота обжима изоляции, мм": [
    "20 AWG: 3,75; 18 AWG: 3,75; 16 AWG: 4,00",
    "20 AWG: 3,4; 18 AWG: 3,4; 16 AWG: 3,4; 15 AWG: 3,4"
   ],
   "Источник высоты обжима": [
    "нет данных производителя: TE Superseal 1.5 — PN отсутствует в таблице Fig.7 спецификации 114-20045 (перечислены только 282110-1/282403-1/282466-1 и парные tab-контакты/284262-1/284263-1)",
    "TE 114-20058 Rev B1, Figure 4, 282308: высота по жиле ±0,05 — 0,5 мм² 1,32; 0,75 — 1,40; 1,0 — 1,49; 1,5 — 1,66; по изоляции 3,4 макс. мм² → AWG по таблице ГОСТ → AWG ERP (0,5 → 20, 0,75 → 18, 1,5 → 16); 15 AWG — верх диапазона контакта (даташит TE: 0,5–1,5 мм² = 20–15 AWG) → строка 1,5 мм²; 1,0 мм² (17 AWG) не край диапазона — в ячейку не входит. До 17.10.2026 высоты стояли «по аналогу» серии. https://web.archive.org/web/20221112083249id_/https://www.te.com/commerce/DocumentDelivery/DDEController?Action=showdoc&DocId=Specification+Or+Standard%7F114-20058%7FB1%7Fpdf%7FEnglish%7FENG_SS_114-20058_B1.pdf%7F282308-1"
   ]
  }
 },
 {
  "id": "5634f683-93e3-443a-8832-bae667137dae",
  "article": "9670008676",
  "edits": {
   "Источник высоты обжима": [
    "нет данных производителя: HARTING D-Sub High-End crimp contact — b2b.harting.com/harting.com отдают 403 на WebFetch без логина; datasheet PDF с высотой обжима не найден через WebSearch (только каталожные страницы с общими описаниями)",
    "Высоты обжима нет по конструкции (17.10.2026): 09 67 000 8676 — точёный контакт D-Sub High-End, обжимается вдавливанием в 4 точках инструментом HARTING 09 99 000 0501 (обжим по MIL-DTL-22520/2-01): размер задаёт матрица, качество проверяют калибром и отрывом, высоты обжима нет. https://www.harting.com/en-US/p/D-SUB-FE-HIGH-END-AWG-20-24-CRIMP-CONT-09670008676 ; инструмент: https://uk.rs-online.com/web/p/crimp-tools/6931137"
   ]
  }
 },
 {
  "id": "4927c000-a7ab-4eb4-8664-22c4331ec5b7",
  "article": "19003-0129",
  "edits": {
   "Источник высоты обжима": [
    "нет данных производителя: ATS-640010400 rev L (11-16-21, ручной инструмент 64001-0400) для Insulkrimp Quick Disconnect даёт только штифтовую калибровку (X-dimension, круглый калибр) по группам сечений — 18-22 AWG: mean .069in/1,75мм (го .066in/1,68 — не-го .072in/1,83мм), это не высота обжима плоским микрометром; сам артикул 19003-0129 в таблице scope этого инструмента не перечислен; product spec PS-19902-008/011/013 содержат только испытание на вырыв, без высоты обжима",
    "Высоты обжима нет по конструкции (17.10.2026): InsulKrimp — полностью изолированный закрытый ствол, Molex контролирует обжим штифтовым калибром, а не высотой: ATS-640010400 rev L (ручной инструмент 64001-0400), группа 18–22 AWG — калибр X 1,68–1,83 мм (среднее 1,75); сам артикул в таблице инструмента не назван; product spec PS-19902 — только испытание на отрыв. https://www.molex.com/en-us/products/part-detail/190030129"
   ]
  }
 },
 {
  "id": "918454d5-abdb-44c2-b7df-8591cd8c7fb5",
  "article": "19003-0135",
  "edits": {
   "Источник высоты обжима": [
    "нет данных производителя: тот же ATS-640010400 rev L; артикул 19003-0135 (BB-2208V) в группе 14-16 AWG — только штифтовая калибровка: mean .092in/2,34мм (го .089in/2,26 — не-го .095in/2,41мм), не высота обжима микрометром; product spec PS-19902 без высоты обжима",
    "Высоты обжима нет по конструкции (17.10.2026): InsulKrimp — полностью изолированный закрытый ствол, Molex контролирует обжим штифтовым калибром, а не высотой: ATS-640010400 rev L (ручной инструмент 64001-0400), группа 14–16 AWG (BB-2208V) — калибр X 2,26–2,41 мм (среднее 2,34); product spec PS-19902 — только испытание на отрыв. https://www.molex.com/en-us/products/part-detail/190030135"
   ]
  }
 },
 {
  "id": "873427fd-6e71-4540-b760-98267d42c1b9",
  "article": "FI-RC3-1A-1E-15000",
  "edits": {
   "Источник высоты обжима": [
    "нет данных производителя: JAE FI-R application specification (mouser datasheet) заблокирована (403/Akamai) при скачивании и через WebFetch; corsa-technic.com не публикует JAE FI-серию",
    "нет данных производителя: JAE FI-R application specification (mouser datasheet) заблокирована (403/Akamai) при скачивании и через WebFetch; corsa-technic.com не публикует JAE FI-серию | 17.10.2026: чертежи JAE с products.jae.com — SJ103060 (FI-RC3-1A-1E-15000) — высоты обжима не дают, на них «USE THE APPLICATOR MADE BY JAE»: условия обжима JAE выдаёт только с аппликатором, открытого документа нет."
   ]
  }
 },
 {
  "id": "cb0e3e30-1779-4690-af80-6364440996ab",
  "article": "FI-RC3-1B-1E-15000",
  "edits": {
   "Источник высоты обжима": [
    "нет данных производителя: JAE FI-R — тот же результат, что и FI-RC3-1A-1E-15000",
    "нет данных производителя: JAE FI-R — тот же результат, что и FI-RC3-1A-1E-15000 | 17.10.2026: чертежи JAE с products.jae.com — SJ103061 (FI-RC3-1B-1E-15000) — высоты обжима не дают, на них «USE THE APPLICATOR MADE BY JAE»: условия обжима JAE выдаёт только с аппликатором, открытого документа нет."
   ]
  }
 },
 {
  "id": "e5495699-a6a2-4ab1-bc51-6cb703e1e850",
  "article": "FI-C3-A1-15000",
  "edits": {
   "Источник высоты обжима": [
    "нет данных производителя: JAE FI-S application specification недоступна — jae.com отдаёт 403, mouser/digikey/rs-online зеркала PDF также блокируют (403/Akamai edgesuite), corsa-technic.com не публикует JAE FI-серию; 16.10.2026, обжим по AWG — уточнения из ячеек: 4,9–9,8 — диапазон на все сечения: 4,9 Н у 32 AWG, 9,8 Н у 28 AWG",
    "нет данных производителя: JAE FI-S application specification недоступна — jae.com отдаёт 403, mouser/digikey/rs-online зеркала PDF также блокируют (403/Akamai edgesuite), corsa-technic.com не публикует JAE FI-серию; 16.10.2026, обжим по AWG — уточнения из ячеек: 4,9–9,8 — диапазон на все сечения: 4,9 Н у 32 AWG, 9,8 Н у 28 AWG | 17.10.2026: чертежи JAE с products.jae.com — SJ030670 (FI-C3-A1-15000) — высоты обжима не дают, на них «USE THE APPLICATOR MADE BY JAE»: условия обжима JAE выдаёт только с аппликатором, открытого документа нет."
   ]
  }
 },
 {
  "id": "ad91fd36-1e51-44b2-8dee-163a2979e695",
  "article": "DF20F-3032SCFA",
  "edits": {
   "Источник высоты обжима": [
    "нет данных производителя: hirose.com API-документы для CL0686-0043-9-04 и -44 не содержат crimping_condition_table (только drawing_2d/spec_sheet/product_manual_guideline/harness_procedure); старый query-URL documenttype=CrimpCondition для DF20/AP105-DF20-2830S (TAD-P-0506, documentid=D144285_en) отдаёт 404 на всём сайте — документ снят с публикации (проверено 2026-09-29); 16.10.2026, обжим по AWG — уточнения из ячеек: 2,9–4,4 — диапазон на все сечения: 2,9 Н у 32 AWG, 4,4 Н у 30 AWG",
    "нет данных производителя: hirose.com API-документы для CL0686-0043-9-04 и -44 не содержат crimping_condition_table (только drawing_2d/spec_sheet/product_manual_guideline/harness_procedure); старый query-URL documenttype=CrimpCondition для DF20/AP105-DF20-2830S (TAD-P-0506, documentid=D144285_en) отдаёт 404 на всём сайте — документ снят с публикации (проверено 2026-09-29); 16.10.2026, обжим по AWG — уточнения из ячеек: 2,9–4,4 — диапазон на все сечения: 2,9 Н у 32 AWG, 4,4 Н у 30 AWG | 17.10.2026: hirose.com: у DF20F-3032SCFA (исполнения 04, 06, 44) Crimp Condition Table нет — она есть только у DF20F-2830SCFA (ETAD-H1079-00, AP105-DF20-2830S); контакт 3032 снят с производства 29.11.2025. Значение 30 AWG в ячейке совпадает со строкой таблицы 2830 (другой контакт) — проверить."
   ]
  }
 },
 {
  "id": "c0a257dd-a965-45b3-a558-a27b3a59c3c7",
  "article": "1586537-1",
  "edits": {
   "Источник высоты обжима": [
    "TE/TTI \"Mini-Universal MATE-N-LOK Connector System\" Quick Reference Guide (02/23), Contacts table p.2 (standard) and Gang Seals table p.6 (sealed) — cached mini-umnl-tti.pdf. REEL=Strip From, BAG=Loose Piece column. Document is a nomenclature/ordering guide only — no crimp height/strip length data (same gap as 770903-1 already in DB, sourced from the same document). Material: brass with pre-tin or gold plating (doc text, p.2); sealed (splash-proof) system, 16 AWG only, gang-seal table p.6",
    "TE/TTI \"Mini-Universal MATE-N-LOK Connector System\" Quick Reference Guide (02/23), Contacts table p.2 (standard) and Gang Seals table p.6 (sealed) — cached mini-umnl-tti.pdf. REEL=Strip From, BAG=Loose Piece column. Document is a nomenclature/ordering guide only — no crimp height/strip length data (same gap as 770903-1 already in DB, sourced from the same document). Material: brass with pre-tin or gold plating (doc text, p.2); sealed (splash-proof) system, 16 AWG only, gang-seal table p.6 | 17.10.2026: TE 408-16017 Rev A, Fig. 2: 1586537-X / 1586538-X — 16 AWG, Ø 1,27–2,11, общий уплотнитель (gang seal), обжим «по 114-16017»; в 114-16017 Rev R такой строки нет (16 AWG — только Fig. 7 для Ø 2,0–3,20, контакты 20–16 AWG) — высота не заполнена. https://web.archive.org/web/20221112121929id_/https://www.te.com/commerce/DocumentDelivery/DDEController?Action=showdoc&DocId=Specification+Or+Standard%7F408-16017%7FA%7Fpdf%7FEnglish%7FENG_SS_408-16017_A.pdf%7F1586537-1"
   ]
  }
 },
 {
  "id": "c30a7082-aad5-4bcc-8082-c7e926ac9ba7",
  "article": "1586538-1",
  "edits": {
   "Источник высоты обжима": [
    "TE/TTI \"Mini-Universal MATE-N-LOK Connector System\" Quick Reference Guide (02/23), Contacts table p.2 (standard) and Gang Seals table p.6 (sealed) — cached mini-umnl-tti.pdf. REEL=Strip From, BAG=Loose Piece column. Document is a nomenclature/ordering guide only — no crimp height/strip length data (same gap as 770903-1 already in DB, sourced from the same document). Material: brass with pre-tin or gold plating (doc text, p.2); sealed (splash-proof) system, 16 AWG only, gang-seal table p.6",
    "TE/TTI \"Mini-Universal MATE-N-LOK Connector System\" Quick Reference Guide (02/23), Contacts table p.2 (standard) and Gang Seals table p.6 (sealed) — cached mini-umnl-tti.pdf. REEL=Strip From, BAG=Loose Piece column. Document is a nomenclature/ordering guide only — no crimp height/strip length data (same gap as 770903-1 already in DB, sourced from the same document). Material: brass with pre-tin or gold plating (doc text, p.2); sealed (splash-proof) system, 16 AWG only, gang-seal table p.6 | 17.10.2026: TE 408-16017 Rev A, Fig. 2: 1586537-X / 1586538-X — 16 AWG, Ø 1,27–2,11, общий уплотнитель (gang seal), обжим «по 114-16017»; в 114-16017 Rev R такой строки нет (16 AWG — только Fig. 7 для Ø 2,0–3,20, контакты 20–16 AWG) — высота не заполнена. https://web.archive.org/web/20221112121929id_/https://www.te.com/commerce/DocumentDelivery/DDEController?Action=showdoc&DocId=Specification+Or+Standard%7F408-16017%7FA%7Fpdf%7FEnglish%7FENG_SS_408-16017_A.pdf%7F1586537-1"
   ]
  }
 },
 {
  "id": "50338da2-c4af-4bcc-ac8d-efbe433d334e",
  "article": "1586538-3",
  "edits": {
   "Источник высоты обжима": [
    "TE/TTI \"Mini-Universal MATE-N-LOK Connector System\" Quick Reference Guide (02/23), Contacts table p.2 (standard) and Gang Seals table p.6 (sealed) — cached mini-umnl-tti.pdf. REEL=Strip From, BAG=Loose Piece column. Document is a nomenclature/ordering guide only — no crimp height/strip length data (same gap as 770903-1 already in DB, sourced from the same document). Material: brass with pre-tin or gold plating (doc text, p.2); sealed (splash-proof) system, 16 AWG only, gang-seal table p.6; no gold pin variant listed for 16 AWG (REEL/BAG both empty for pin) — only socket gold exists, loose-piece only",
    "TE/TTI \"Mini-Universal MATE-N-LOK Connector System\" Quick Reference Guide (02/23), Contacts table p.2 (standard) and Gang Seals table p.6 (sealed) — cached mini-umnl-tti.pdf. REEL=Strip From, BAG=Loose Piece column. Document is a nomenclature/ordering guide only — no crimp height/strip length data (same gap as 770903-1 already in DB, sourced from the same document). Material: brass with pre-tin or gold plating (doc text, p.2); sealed (splash-proof) system, 16 AWG only, gang-seal table p.6; no gold pin variant listed for 16 AWG (REEL/BAG both empty for pin) — only socket gold exists, loose-piece only | 17.10.2026: TE 408-16017 Rev A, Fig. 2: 1586537-X / 1586538-X — 16 AWG, Ø 1,27–2,11, общий уплотнитель (gang seal), обжим «по 114-16017»; в 114-16017 Rev R такой строки нет (16 AWG — только Fig. 7 для Ø 2,0–3,20, контакты 20–16 AWG) — высота не заполнена. https://web.archive.org/web/20221112121929id_/https://www.te.com/commerce/DocumentDelivery/DDEController?Action=showdoc&DocId=Specification+Or+Standard%7F408-16017%7FA%7Fpdf%7FEnglish%7FENG_SS_408-16017_A.pdf%7F1586537-1"
   ]
  }
 },
 {
  "id": "6efd455c-6d4f-4887-b969-1cba1e931070",
  "article": "163303-6",
  "edits": {
   "Источник высоты обжима": [
    "нет данных производителя: TE Commercial MATE-N-LOK — та же спецификация 114-1012, тот же блок доступа (см. 350078-1); 16.10.2026, качество данных: «Тип контакта» «Male (Pin) — см. notes» → «Male»: TE, даташит 163303-6: «Pin Contact, 17 – 13 AWG … Commercial MATE-N-LOK» (ответное гнездо — 163302-6); terGender уже Male",
    "нет данных производителя: TE Commercial MATE-N-LOK — та же спецификация 114-1012, тот же блок доступа (см. 350078-1); 16.10.2026, качество данных: «Тип контакта» «Male (Pin) — см. notes» → «Male»: TE, даташит 163303-6: «Pin Contact, 17 – 13 AWG … Commercial MATE-N-LOK» (ответное гнездо — 163302-6); terGender уже Male | 17.10.2026: даташит TE ссылается только на 114-1012 Rev H, в его таблицах этого номера нет; чертёж C-163555 rev B2 (AMP Holland, «для миниатюрных аппликаторов») высоты не даёт, C-163303 в Web Archive нет."
   ]
  }
 },
 {
  "id": "dbaf8801-4dce-4b88-a892-eb8eba0798b7",
  "article": "163555-6",
  "edits": {
   "Источник высоты обжима": [
    "нет данных производителя: TE Commercial MATE-N-LOK — та же спецификация 114-1012, тот же блок доступа (см. 350078-1); 16.10.2026, качество данных: «Тип контакта» «Male (Pin) — см. notes, отличается от типового Female у остальных строк листа» → «Male»: TE, даташит 163555-6 (te.com.cn): «Pin Contact … 22 – 18 AWG … Commercial MATE-N-LOK»; RS: «Male Crimp Terminal, 22AWG Min, 18AWG Max»; terGender уже Male",
    "нет данных производителя: TE Commercial MATE-N-LOK — та же спецификация 114-1012, тот же блок доступа (см. 350078-1); 16.10.2026, качество данных: «Тип контакта» «Male (Pin) — см. notes, отличается от типового Female у остальных строк листа» → «Male»: TE, даташит 163555-6 (te.com.cn): «Pin Contact … 22 – 18 AWG … Commercial MATE-N-LOK»; RS: «Male Crimp Terminal, 22AWG Min, 18AWG Max»; terGender уже Male | 17.10.2026: даташит TE ссылается только на 114-1012 Rev H, в его таблицах этого номера нет; чертёж C-163555 rev B2 (AMP Holland, «для миниатюрных аппликаторов») высоты не даёт, C-163303 в Web Archive нет."
   ]
  }
 },
 {
  "id": "131c016f-9aff-4b74-9969-89c15091c9fc",
  "article": "60294-1",
  "edits": {
   "Источник высоты обжима": [
    "нет данных производителя: TE FASTIN-FASTON .250 application specification (114-8004/114-1012 диапазон) не найдена в открытом доступе; te.com/rs-online блокируют, corsa-technic.com не публикует кримп-данные для этой серии",
    "нет данных производителя: TE FASTIN-FASTON .250 application specification (114-8004/114-1012 диапазон) не найдена в открытом доступе; te.com/rs-online блокируют, corsa-technic.com не публикует кримп-данные для этой серии | 17.10.2026: чертёж C-60294 rev T и 114-2025 Rev G3 (Web Archive): высота обжима — «по листу аппликатора»; лист аппликатора в открытом доступе не найден."
   ]
  }
 }
]''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
done = same = refused = 0
for row in ROWS:
    with factory() as uow:
        record = uow.tech_operations.get(UUID(row["id"]))
        if record is None:
            refused += 1
            print(f"   нет записи: {row['article']}")
            continue
        fields = record.fields
        article = str(fields.get("terArticle") or fields.get("Артикул контакта (BAG)") or "").strip()
        if article != row["article"]:
            refused += 1
            print(f"   отказ: артикул «{article}», ждали «{row['article']}»")
            continue
        edits = row["edits"]
        if all(str(fields.get(name, "")).strip() == new.strip() for name, (_, new) in edits.items()):
            same += 1
            print(f"   уже так: {row['article']}")
            continue
        try:
            before, _ = _edit_in(record.id, tuple(FieldEdit(name, old, new) for name, (old, new) in edits.items()), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            refused += 1
            print(f"   отказ: {row['article']} ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(before.id), "tab": before.tab.value, "fields": list(edits), "reason": REASON}, uow)
        if mode == "apply":
            uow.commit()
        done += 1
        print(f"   {'проверено' if mode == 'dry' else 'записано'}: {row['article']}: {', '.join(edits)}")
print(f"ИТОГО {len(ROWS)}: {'проверено' if mode == 'dry' else 'записано'} {done}, уже так {same}, отказ {refused}")
