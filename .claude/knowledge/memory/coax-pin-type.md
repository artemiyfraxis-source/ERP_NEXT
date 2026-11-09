---
name: coax-pin-type
description: Owner 20.10 — БД.КОАКС «Тип пина»: angled = «пайка внутри корпуса» (never crimp), 2.92/3.5 mm = «цанга»; a seller's «обжимной» means the shield ferrule, not the pin
metadata:
  type: feedback
---

«Тип пина» in БД.КОАКС, by construction:
- angled (R/A) cable connector → «пайка внутри корпуса»: the centre conductor is soldered into the contact through the
  body window under the cap. Never «обжим». Owner 20.10: «как у углового может быть обжим пина, ты наобум писал?»
- Exception (owner 25.10): FAKRA angled jacks whose centre pin is a separate part crimped onto the conductor before the
  cable goes into the angled body (Rosenberger 59K25H-1M4A4, MA_59V065: square crimp 1,1, insert 11W150-504, tip seen in
  the window, then cap) → «обжим». Owner: «если пин отдельно вставляется — конечно исключение». Conversely Amphenol
  FAKRA straight bulkhead 2FA1-NZSP-C30E2 solders the pin despite «Crimp» in its title.
- 2,92 / 3,5 mm on semi-rigid → «цанга»: the contact is pushed onto the conductor, no solder. Owner 20.10: «у 3.5 и 2.92
  там вообще не может быть пайки пина … цанга».
- F-type: the pin is the cable core itself, so leave the field empty. Twist-on «под винт» (BNC-RJ): leave empty too.

**Why:** gap-fill passes on 20.10 copied «обжимной» from kroks titles («…обжимной, угловой») into «Тип пина». That word
is about the shield ferrule. The result was 18 angled rows with a crimped pin, and the generator read «цанга» as «пайка».
Fixed on prod the same day; backup erp_2026-10-20_125510.dump.
**How to apply:** fill «Тип пина» only from a statement about the centre contact («центральный контакт — пайка /
цанговый / обжимной контакт»). Never take it from a seller's crimp/solder word about the whole connector. Check
angled rows against the rule above before any write. Related: [[coax-contact-depth]], [[coax-data-sources]].

«Тип экрана» (owner 26.10): only three — обжим, пайка, прижим. «накрутка, самообжим, корпус, компрессия — так это всё прижим»: the 35 БД.КОАКС rows with those values were set to «прижим» on prod 26.10 (journal docs/data «КОАКС — тип экрана редкие → прижим 26.10.csv»); never add other shield types.
