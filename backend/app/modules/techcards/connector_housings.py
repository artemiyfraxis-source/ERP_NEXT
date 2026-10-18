"""Connector-family pin layouts ported from current SUPPLYMAP TcHousing.gs."""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Mapping

from .connector_catalog_data import CATALOG_FAMILIES, CATALOG_FAMILY_SAMPLES, MINIFIT_CAV


IVORY = "#efe6cf"
BLACK = "#2b2b2b"
ALIASES = {"LP-12": "LP-12-C02PE"}
# Analogue -> original the housing is drawn from (TcHousing.gs HOUSING_ALIAS_RULES_): BL2018P-nn is an analogue of
# the JST SMP-nnV-BC plug. Applied after ALIASES and before family detection, on the normalised article.
ALIAS_RULES: tuple[tuple[str, str], ...] = ((r"^BL2018P-0*(\d+)", r"SMP-0\1V-BC"),)


def _jst(identifier: str, series: str, pattern: str, pitch: float, latch: str, **extra) -> dict[str, Any]:
    return {
        "id": identifier, "title": f"JST {series}", "pattern": pattern,
        "pitch": pitch, "rows": 1, "order": "row", "start": "TL",
        "latch": latch, "color": IVORY, "conf": "low",
        "note": "по общему правилу JST: со стороны проводов контакт 1 слева",
        **extra,
    }


# A series checked by the owner on the real part where the maker's sheet has no circuit-1 mark (17.10.2026).
OWNER_PART = "сверено владельцем по детали, 17.10.2026"

FAMILIES: tuple[dict[str, Any], ...] = (
    # GH, XH, PH, ZH — checked against the JST catalogue sheets (eGH, eXH, ePH, eZH «Housing», copies of the JST PDFs
    # found on GitHub, 01.10). Each front view has the wire end up; GH and XH show the lock face, PH and ZH the face with
    # the contact-lance windows (ZH moulds the circuit numbers there). Turned to the wire side: lock up (GH, XH) or
    # windows down (PH, ZH) -> contact 1 on the LEFT, as before. PH/ZH cross-check: their header front view has circuit 1
    # on the right, so the mated housing shows the header its smooth back — the windows face away from the header wall.
    # `body`: B = A + extra (A = (n−1)·pitch), h — thickness at the wire end without the lock, lock_* — the lock on top.
    _jst("jst-gh", "GH", r"^GHR-0*(\d+)V-S", 1.25, "ramp", conf="high", src="JST eGH, Housing", model="plate",
         note="по чертежу JST eGH: защёлка сверху, контакт 1 слева",
         body={"extra": 2.5, "h": 2.85, "len": 5.7, "lock_inset": 2.5, "lock_h": 1.3, "win": (0.7, 1.2)}),
    # SH — checked against the JST eSH housing drawing (owner's screenshot, 30.09): the top view has the wire end with
    # the protrusions at the top and Circuit No.1 ▽ on the left, so from the wire side with the ▽ face up contact 1 is
    # on the RIGHT (SUPPLYMAP draws it on the left — mirrored). Body from the same sheet: B = A + 4.0 with protrusions
    # (-B), A + 2.0 without; 2.8 thick at the wire end, 2.1 at the mating part, 5 long.
    _jst("jst-sh", "SH", r"^SHR-?0*(\d+)V-S", 1.0, "mark", start="TR", conf="high", model="slots",
         note="по чертежу JST eSH: метка ▽ у контакта 1", src="JST eSH, Housing",
         body={"extra": 2.0, "h": 2.8, "rear_h": 2.1, "len": 5.0, "front_len": 1.8, "ear_len": 1.0, "win": (0.6, 1.3)},
         body_by=((r"V-S-B", {"ears": 1.0}),)),
    # NSH — JST eNSH «Housing» (jst-mfg.com/product/pdf/eng/eNSH.pdf, p. 2): front view = lock face, wire end up; the
    # «Circuit No.1 mark» is a groove on the end wall seen in the right side view (third-angle) -> circuit 1 at the right
    # of the front view; from the wire side, lock up, contact 1 is on the LEFT (as GH). B = A + 3.0; 4.2 thick with the
    # lock, 3.6 without; 6.2 long; lock 4.4 wide (measured off the 4–9 circuit view). NSHR-02V-S is the 3-circuit housing
    # with the middle cavity plugged (contacts 2.0 apart), so its B is 5.0.
    _jst("jst-nsh", "NSH", r"^NSHR-0*(\d+)V-S", 1.0, "ramp", conf="high", src="JST eNSH, Housing", model="plate",
         note="по чертежу JST eNSH: защёлка сверху, контакт 1 слева (метка на торце корпуса)",
         body={"extra": 3.0, "h": 3.6, "len": 6.2, "lock_w": 4.4, "lock_h": 0.6, "win": (0.6, 1.1)},
         body_by=((r"^NSHR-0*2V", {"extra": 4.0}),)),
    _jst("jst-xh", "XH", r"^XHP-(\d+)", 2.5, "ramp", conf="high", src="JST eXH, Housing", model="frame",
         note="по чертежу JST eXH: защёлка сверху, контакт 1 слева",
         body={"extra": 4.8, "h": 4.1, "len": 7.75, "lock_inset": 1.8, "lock_h": 1.6, "win": (1.2, 1.2), "cav": {"kind": "bevel", "w": 1.2, "h": 1.2}}),
    _jst("jst-ph", "PH", r"^PHR-(\d+)", 2.0, "windows", conf="high", src="JST ePH, Housing", model="slots",
         note="по чертежу JST ePH: окна фиксаторов снизу, контакт 1 слева",
         body={"extra": 3.8, "h": 4.5, "len": 6.85, "win": (0.9, 2.1), "cav": {"kind": "slot", "w": 0.9, "h": 2.1}}),
    # EH — JST eEH «Housing»: front view with the wire end up shows the face with the contact windows and the moulded
    # circuit numbers (2, 3, … left to right; No. 1 circuit on the left); the locks are ramps on the ends (side view and
    # the header's end-wall hooks), not on a face. Turned to the wire side with that face down: contact 1 on the LEFT,
    # as before; the face is drawn like PH/ZH («windows»). B = A + 4.5, 3.8 thick, 6.5 long; `win` is a sketch size.
    _jst("jst-eh", "EH", r"^EHR-(\d+)", 2.5, "windows", conf="high", src="JST eEH, Housing", model="slots",
         note="по чертежу JST eEH: окна и цифры контактов на одной грани, защёлки на торцах; окнами вниз — контакт 1 слева",
         body={"extra": 4.5, "h": 3.8, "len": 6.5, "win": (1.3, 1.6)}),
    _jst("jst-zh", "ZH", r"^ZHR-(\d+)", 1.5, "windows", conf="high", src="JST eZH, Housing", model="slots",
         note="по чертежу JST eZH: окна фиксаторов и цифры снизу, контакт 1 слева",
         body={"extra": 3.0, "h": 3.4, "len": 4.8, "win": (0.8, 1.3)}),
    # VH — JST eVH «Housing» (N and M type): front view shows the lock lever on the front face; the view below it (third
    # angle, from the mating end, lever face on top) has No. 1 circuit on the right. From the wire side, lever up, that
    # mirrors to contact 1 on the LEFT (as before). Only the overall size is dimensioned, so no `body`.
    _jst("jst-vh", "VH", r"^VHR-(\d+)", 3.96, "lever", conf="high", src="JST eVH, Housing",
         note="по чертежу JST eVH: вид со стыковки рычагом вверх — контакт 1 справа; со стороны проводов рычагом вверх — слева"),
    # PA — JST ePA-F «PA connector/ Housing (Standard type)»: front view with the flanged wire end up shows the lock lever
    # on the front face and No. 1 circuit on the right (the header's lock window and No. 1 match it). From the wire side,
    # lever up: contact 1 on the LEFT (as before). The lever sits under the flange, so the generic lever sketch stays.
    _jst("jst-pa", "PA", r"^PAP-0*(\d+)V", 2.0, "lever", conf="high", src="JST ePA-F, Housing (Standard type)",
         note="по чертежу JST ePA-F: рычаг защёлки на лицевой грани, контакт 1 на ней справа; со стороны проводов рычагом вверх — слева"),
    # ZE — JST eZE «Socket housing» (p. 3): front view = the face with the big outer lock lever, wire end up; «Mark of
    # No. 1 circuit» is a groove on the end wall in the right side view -> circuit 1 at the right of the front view. The
    # header sheet agrees (▽ at the right, lock window on the same face). From the wire side, lever up: contact 1 LEFT.
    _jst("jst-ze", "ZE", r"^ZER-0*(\d+)V", 1.5, "lever", conf="high", src="JST eZE, Socket housing",
         note="по чертежу JST eZE: рычаг замка сверху, контакт 1 слева (метка на торце корпуса)"),
    # CZ — JST eCZ-F p. 4 (read 16.10.2026 from jst-mfg.com): the CZH crimp housing (CZHR) is drawn without a circuit-1
    # mark, but the sheet states the CZH housing is interchangeable with the CZ IDC socket on the same header, and the CZ
    # socket drawn right above it has «No.1 circuit» ▽ at the LEFT of its front face (wire end up, IDC slots at the top).
    # Both side views put the lock on the back face, so the two housings sit on the header the same way: circuit 1 at the
    # left of the front view; from the wire side, lock (back face) up, contact 1 on the LEFT (as before).
    _jst("jst-cz", "CZ", r"^CZHR-0*(\d+)V", 1.5, "ramp", conf="high", src="JST eCZ-F, CZ Socket + CZH Housing",
         note="по чертежу JST eCZ-F: CZH взаимозаменяем с розеткой CZ, у неё метка ▽ контакта 1 слева на лицевой грани; со стороны проводов защёлкой вверх — контакт 1 слева"),
    # PNI — JST ePNI-WB «Socket housing» (p. 2): front view = lock-lever face, wire end up, «No. 1 circuit» points at the
    # rightmost cavity -> from the wire side, lever up, contact 1 on the LEFT. B = A + 3.7; 6.45 thick without the
    # lever's release tab (0.8 more), 10.05 long; the tab is 3.0 wide.
    _jst("jst-pni", "PNI", r"^PNIRP-0*(\d+)V", 2.0, "lever", conf="high", src="JST ePNI-WB, Socket housing",
         note="по чертежу JST ePNI-WB: рычаг замка сверху, контакт 1 слева",
         body={"extra": 3.7, "h": 6.45, "len": 10.05, "lock_w": 3.0, "lock_h": 0.8, "win": (1.0, 1.6)}),
    # RCY — JST eRCY «Housing» (p. 2): the top views show «1 2» moulded on the flat face, wire end on the right. SYR
    # (receptacle, pin contacts): 1 at the top of the view; SYP (plug, socket contacts): 1 at the bottom. From the wire
    # side with the numbered face up that is 1 on the RIGHT for SYR and on the LEFT for SYP — mirrored, as a mated pair
    # with both numbered faces on one side must be. The drawing shows no latch, so the marker is the numbered face.
    # SY?-02TV — white (natural) V-0 version. Titles keep «(…)» so the warehouse name stays «RCY Npin» (Stock05 parity).
    _jst("jst-rcy", "RCY (SYR, штыри)", r"^SYR-0*(\d+)T", 2.5, "mark", start="TR", color="#c8322d",
         color_by=((r"\dTV", IVORY),), conf="high", src="JST eRCY, Housing",
         note="по чертежу JST eRCY: грань с цифрами 1, 2 сверху — контакт 1 справа"),
    _jst("jst-rcy-p", "RCY (SYP, гнёзда)", r"^SYP-0*(\d+)T", 2.5, "mark", color="#c8322d",
         color_by=((r"\dTV", IVORY),), conf="high", src="JST eRCY, Housing",
         note="по чертежу JST eRCY: грань с цифрами 1, 2 сверху — контакт 1 слева"),
    # SM — JST eSM (read 17.10.2026 via Web Archive), p. 3 «Housing» (A = (n−1)·2.5) and p. 1 (picture of the mated pair,
    # «Panel Layout and Assembly layout»); cross-checked with the owner's product photos of SMR-05V-B and SMP-02.
    # SMR «Receptacle housing (for pin contact)»: the HOOD at the mating end — B = A + 5.5, 7.5 thick, 8 long — and the
    # narrower stepped body at the wire end — B = A + 3.0, 10.2 long (18.2 overall); on top in the middle the rocker
    # lock lever with longitudinal ribs (end view: the tab 3.8 wide; side view: from the mouth 14.5 back; assembly:
    # 8.7 over the lever, i.e. 1.2 above the hood). The body thickness behind the hood is not dimensioned — 6.0 is read
    # off the assembly sketch (`h`).
    # SMP «Plug housing (for socket contact)»: B = A + 3.0, 7.4 thick, 11.7 long; a flange at the wire end and a thinner
    # nose (panel hole 5.15 high on p. 1) that goes into the SMR hood, the lock catch on its top at the mating end
    # (top view: the small block on the mating edge), and on BOTH row ends the flexible panel arms flaring out 3.2 with the
    # stepped grips at the wire end (panel thickness 0.5–2.0 → three steps; end view: arms 4 wide).
    _jst("jst-sm-r", "SM (вилка)", r"^SMR-0*(\d+)V", 2.5, "lever", max=12, start="TL", conf="high", color_by=((r"V-B", BLACK),),
         src="JST eSM, Housing", model="hood",
         body={"extra": 3.0, "h": 6.0, "len": 18.2, "win": (1.0, 1.0), "lock_w": 3.8, "lock_h": 1.2, "lock_len": 14.5,
               "hood": {"extra": 5.5, "h": 7.5, "len": 8.0, "wall": 0.8}}),
    _jst("jst-sm-p", "SM (розетка)", r"^SMP-0*(\d+)V", 2.5, "lever", max=12, start="TR", conf="high", color_by=((r"V-B", BLACK),),
         src="JST eSM, Housing", model="arms",
         body={"extra": 3.0, "h": 7.4, "len": 11.7, "win": (1.1, 1.1), "lock_w": 3.0, "lock_h": 0.6, "lock_len": 1.3,
               "nose_h": 5.2, "front_len": 1.4, "wings": {"out": 3.2, "w": 4.0, "root": 4.3, "tip": 1.1}}),
    # PicoBlade 51021 — Molex 510210000-SD (third angle): the top view is the wire end, with the face of the contact
    # windows (front view) at the bottom, the friction-lock bumps on the opposite face and CIRCUIT 1 (moulded «1» mark)
    # on the right; the bottom view has CIRCUIT 1 on the right too. From the wire side, windows down: contact 1 on the
    # RIGHT (as before). Flange A = B + 3.0 (B = (n−1)·1.25), 3.2 thick, 3.95 long; `win` is a sketch size.
    {"id": "molex-picoblade", "title": "Molex PicoBlade", "pattern": r"^51021-?0?(\d{1,2})00$", "pitch": 1.25, "rows": 1, "order": "row", "start": "TR", "latch": "windows", "color": IVORY, "conf": "high",
     "src": "Molex 510210000-SD", "note": "по чертежу Molex 510210000-SD: на торце проводов метка «1» у контакта 1; окнами вниз (выступы защёлки вверх) — контакт 1 справа",
     "body": {"extra": 3.0, "h": 3.2, "len": 3.95, "win": (0.9, 1.4)}},
    # PicoBlade 51047 (wire-to-wire plug housing) — Molex 510470000-SD rev. B (third angle; static.chipdip.ru copy, read
    # 16.10.2026): the top view (mating shroud with the pins) and the bottom view (wire end) both have CIRCUIT 1 at the
    # RIGHT; the bottom view puts the «CIRCUIT 1 INDICATOR MARK» notch on the back-face edge at that corner. From the wire
    # side with the mark (back) face up the view turns over: contact 1 on the LEFT, under the mark. Was TR with no face
    # cue (mirrored against the mark).
    {"id": "molex-picoblade-plug", "title": "Molex PicoBlade", "pattern": r"^51047-?0?(\d{1,2})00$", "pitch": 1.25, "rows": 1, "order": "row", "start": "TL", "latch": "mark", "color": IVORY, "conf": "high",
     "src": "Molex 510470000-SD", "note": "по чертежу Molex 510470000-SD: метка CIRCUIT 1 на торце проводов у контакта 1; меткой вверх — контакт 1 слева"},
    # Mini-Fit Jr. 5557 dual row — Molex SD-5557-003 (third angle): the view above the front view (wire end) has the latch
    # at the bottom, circuits 1…n in the far row and n+1…2n in the latch row, 1 and n+1 on the left. Turned latch up: 1
    # bottom-right, rows right to left (as before). A = B + 5.4 (B = (cols−1)·4.2), 11.6 across the rows at the wire end,
    # 19.6 long, latch 5.4 wide and 2.8 proud (measured on the 4:1 drawing); `win` is a sketch size.
    {"id": "molex-minifit-jr", "title": "Molex Mini-Fit Jr.", "pattern": r"^39-?01-?2(\d{2})\d$|^5557-(\d{2})R", "count_groups": (1, 2), "pitch": 4.2, "rows": 2, "order": "row", "start": "BR", "latch": "lever", "color": IVORY, "conf": "high",
     "src": "Molex SD-5557-003", "note": "по чертежу Molex SD-5557-003: защёлка сверху, контакт 1 внизу справа, ряд 1…n дальше от защёлки",
     "body": {"extra": 5.4, "h": 11.6, "len": 19.6, "lock_w": 5.4, "lock_h": 2.8, "win": (3.4, 3.4), "cav": MINIFIT_CAV}},
    # Micro-Fit 43025 — Molex sales drawing SDA-43025: mating view (top view, latch up) has circuit 1 bottom-left and the
    # last circuit top-right; from the wire side that mirrors to 1 bottom-right, rows counted right to left (as before).
    # Width (A) = 3.00 per column + 0.85, 6.86 across the rows, 14.0 long, latch 3.86 wide.
    {"id": "molex-microfit", "title": "Molex Micro-Fit 3.0", "pattern": r"^43025-?(\d{2})0\d$", "pitch": 3.0, "rows": 2, "order": "row", "start": "BR", "latch": "lever", "color": BLACK, "conf": "high",
     "src": "Molex SDA-43025", "note": "по чертежу Molex SDA-43025: защёлка сверху, контакт 1 внизу справа",
     "body": {"extra": 3.85, "h": 6.86, "len": 14.0, "lock_w": 3.86, "lock_h": 1.4, "win": (2.2, 2.2)}},
    # Micro-Fit 43645 — Molex 436450000-SD (replaces SDA-43645; third angle): the front view is the latch face with the
    # wire end up, the view under it (mating towers, latch at its top edge) has CIRCUIT #1 at the left, and the rear view
    # carries the FIRST CIRCUIT IDENTIFIER rib on that side. From the wire side, latch up: contact 1 on the RIGHT.
    {"id": "molex-microfit-1r", "title": "Molex Micro-Fit 3.0 (1 ряд)", "pattern": r"^43645-?0?(\d{1,2})00$", "pitch": 3.0, "rows": 1, "order": "row", "start": "TR", "latch": "lever", "color": BLACK, "conf": "high",
     "src": "Molex 436450000-SD (SDA-43645)", "note": "по чертежу Molex 436450000-SD: со стыковки защёлкой вверх CIRCUIT #1 слева; со стороны проводов защёлкой вверх — контакт 1 справа"},
    # SL dual row 22-55-2xx1 — Molex SD-70450-0001-0026 (pdm_docs copy of 022552081_sd.pdf in the Web Archive, read
    # 17.10.2026, third angle): the top view is the mating end (square post entries), the front view the face with the
    # ▽ «CKT. NO. 1» at its LEFT and a window over every column. From the wire side (bottom view, left and right kept),
    # that face up: contact 1 top-LEFT. Where circuit 2 is the drawing does not say — under 1, by the Molex rule for
    # dual-row housings (C-Grid), hence "medium". Was TR with no face cue (mirrored against the ▽).
    {"id": "molex-sl-2r", "title": "Molex SL (2 ряда)", "pattern": r"^22-?55-?2(\d{2})1$", "pitch": 2.54, "rows": 2, "order": "col", "start": "TL", "latch": "friction", "color": BLACK, "conf": "medium", "src": "Molex SD-70450-0001-0026",
     "note": "по чертежу Molex SD-70450-0001-0026: ▽ CKT. NO. 1 слева на грани с окнами; этой гранью вверх — контакт 1 слева вверху; № 2 под ним — по правилу Molex, на чертеже не показан"},
    {"id": "te-multilock-040", "title": "TE Multilock 040", "pattern": r"^1740(?:47|45|46|44)-\d$", "fixed": 20, "pitch": 2.5, "rows": 2, "order": "col", "start": "TR", "latch": "lever", "color": BLACK, "conf": "low"},
    # DS1071 — Connfly C-DS1071-XXXXX-B rev. B (connfly.com/static/upload/file/pdf/DS1071.pdf, read 16.10.2026): pitch
    # 2.54; the female housing is drawn symmetric — no key, no circuit-1 mark — and the mating headers C-DS1021 / C-DS1022
    # (through-hole) have no mark either (only the SMT sheet 5 of DS1022 marks «No.1» on the header). The housing goes on
    # either way round, so contact 1 is set by the board's marking, not by the housing: the numbering stays unchecked.
    {"id": "connfly-ds1071", "title": "Connfly DS1071 (Dupont 2,54)", "pattern": r"^I-DS1071-SC[BW]0?(\d)X0?(\d{1,2})", "grid": True, "pitch": 2.54, "order": "col", "start": "TL", "latch": "none", "color": BLACK, "conf": "high",
     "src": OWNER_PART,
     "note": "сверено владельцем по детали 17.10.2026: прорезями вверх контакт 1 слева — у стрелки на корпусе"},
    # DS1069 — Connfly C-DS1069-XXXXX-E p. 2 «Female Housing» (connfly.com/static/upload/file//DS1069.pdf): front view =
    # face with the lock, wire end up; the bottom (mating) view puts «Circuit No.1 Mark» at the right end -> from the
    # wire side, lock up, contact 1 on the LEFT, exactly as JST eXH. Same sizes as XH: B = N·2.5 + 2.3, 4.1 thick
    # (5.7 with the lock), 7.8 long.
    {"id": "connfly-ds1069", "title": "Connfly DS1069 (аналог JST XH)", "pattern": r"^I-DS1069-SC[BW]0*(\d+)", "pitch": 2.5, "rows": 1, "order": "row", "start": "TL", "latch": "ramp", "color": IVORY, "conf": "high", "model": "frame",
     "src": "Connfly DS1069 + JST eXH", "note": "по чертежу Connfly DS1069 (метка Circuit No.1) и JST eXH: защёлка сверху, контакт 1 слева",
     "body": {"extra": 4.8, "h": 4.1, "len": 7.8, "lock_inset": 1.8, "lock_h": 1.6, "win": (1.2, 1.6)}, "color_by": ((r"-SCB", BLACK),)},
    # DS1066 — Connfly C-DS1066-XXXXXXX-C sheet 2 «Female Housing» (connfly.com, read 16.10.2026; PH type): the front view
    # (wire end up) shows the face with the contact-lance windows and «Pin1» at its LEFT — exactly as JST ePH. From the
    # wire side, windows down: contact 1 on the LEFT. Was TR with no face cue.
    {"id": "connfly-ds1066", "title": "Connfly DS1066 (шаг 2,0)", "pattern": r"^I-DS1066-SC[BWE]0*(\d+)", "pitch": 2.0, "rows": 1, "order": "row", "start": "TL", "latch": "windows", "color": IVORY, "erp_only": True, "conf": "high",
     "src": "Connfly C-DS1066, Female Housing", "color_by": ((r"-SCB", BLACK),),
     "note": "по чертежу Connfly DS1066: Pin1 слева на грани с окнами фиксаторов (как JST PH); окнами вниз — контакт 1 слева"},
    # Amass XT / QS8: the Amass product specifications (XT60, XT60H 2021V1) show the faces without numbers or «+»/«−»,
    # so the contacts stay unlabelled and unchecked.
    {"id": "amass-xt", "title": "Amass XT", "pattern": r"^XT-?(30|60|90)", "fixed": 2, "rows": 1, "order": "row", "start": "TR", "latch": "none", "color": "#f2c200", "cavity": "round", "labels": ("", ""), "conf": "low",
     "note": "в спецификациях Amass XT60/XT60H полярность и номера не указаны — «+»/«−» по маркировке на корпусе"},
    {"id": "amass-qs8", "title": "Amass QS8", "pattern": r"^QS8", "fixed": 2, "rows": 1, "order": "row", "start": "TR", "latch": "none", "color": "#d7322d", "cavity": "round", "labels": ("", ""), "conf": "low",
     "note": "чертёж Amass QS8 не найден — полярность по маркировке на корпусе"},
    {"id": "rj45", "title": "RJ45 8P8C", "pattern": r"^8P8C|^RJ-?45", "fixed": 8, "pitch": 1.02, "rows": 1, "order": "row", "start": "TR", "latch": "lever", "color": "#dfe8ea", "conf": "high"},
    {"id": "weipu-sa8", "title": "Weipu SA8", "pattern": r"^SA81[0125]/([PS])(\d)", "kind": "round", "count_group": 2, "male_group": 1, "round": {2: ((.45, 0), (-.45, 0)), 3: ((-.36, -.22), (0, .43), (.37, -.22)), 4: ((-.32, -.33), (-.32, .32), (.32, .32), (.32, -.33)), 6: ((-.29, -.44), (-.47, .14), (0, .49), (.49, .14), (.31, -.44), (0, 0)), 8: ((-.26, -.55), (-.58, -.13), (-.47, .38), (0, .61), (.47, .38), (.58, -.13), (.26, -.55), (0, 0))}, "color": BLACK, "conf": "high"},
    # LP-12: the Linko drawing LP-12-xxxPE-02-0xxx (2024) numbers the male and female faces, but they are not mirror
    # images of each other (2 pins mirror, 3 pins do not) and its 4-pin male face is a square, not the catalogue diamond
    # drawn here — which side the views show cannot be told, so the layout stays unchecked.
    {"id": "cnlinko-lp12", "title": "CNLINKO LP-12", "pattern": r"^LP-12-[CJ]0?(\d)([PS])", "kind": "round", "count_group": 1, "male_group": 2, "round": {2: ((.36, -.09), (-.33, -.09)), 3: ((.37, -.21), (-.33, -.21), (.03, .41)), 4: ((0, -.4), (.42, 0), (-.4, 0), (0, .41))}, "color": BLACK, "conf": "medium",
     "note": "чертёж Linko LP-12-xxxPE-02-0xxx: виды штырей и гнёзд не зеркальны, 4 конт. квадратом — сторона вида не определена; номера на изоляторе"},
    # USB-A — USB 2.0 Specification, fig. 6-9 (Series A plug: insulator below, contacts 4-3-2-1 left to right on the
    # mating face) and fig. 6-7 (receptacle: tongue on top, 1-2-3-4); table 6-1 assigns VBUS, D−, D+, GND. From the solder
    # side with the plastic part down, contact 1 is on the LEFT for the plug and the receptacle alike (as before); the
    # sketch has no face cue, so the title says it (the warehouse name drops the bracket).
    {"id": "usb-a", "title": "USB-A 2.0 (пластик снизу)", "pattern": r"^USB-?A", "fixed": 4, "rows": 1, "order": "row", "start": "TL", "latch": "none", "color": "#c9ccd0", "labels": ("+5В", "D−", "D+", "GND"), "conf": "high",
     "src": "USB 2.0 Specification, рис. 6-7, 6-9, табл. 6-1", "note": "по чертежам USB 2.0: со стороны пайки, пластиком вниз — контакт 1 (+5В) слева, у вилки и у розетки"},
    # Series bought for real projects (SUPPLYMAP purchase summary, 30.09.2026) that had no drawing. Numbering of these
    # is by the family's general rule, not checked against each drawing — conf "low" keeps the warning on the sketch.
    # `erp_only`: drawn in the ERP but not in SUPPLYMAP TcHousing.gs, so the warehouse name autofill (Stock05 parity) skips them.
    # PHD — JST ePHD «Socket housing»: front view with the wire end up shows the face with the moulded circuit numbers
    # (the odd row: 1 on the right, 9 further left) and the ▽ «Mark of No. 1 circuit» above the right column; the side
    # view has No. 1 in the row at that face and No. 2 behind it. From the wire side, ▽ face up: contact 1 top-LEFT, 2
    # under it, odd numbers along the top row (as before). The locks are on the ends, so the ▽ is the face cue.
    # B = A + 3.9, 5 across the rows, 7.3 long; `win` is a sketch size.
    {"id": "jst-phd", "title": "JST PHD (2 ряда)", "pattern": r"^PHDR-(\d+)VS", "pitch": 2.0, "rows": 2, "order": "col", "start": "TL", "latch": "mark", "color": IVORY, "erp_only": True, "conf": "high",
     "src": "JST ePHD, Socket housing", "note": "по чертежу JST ePHD: метка ▽ и цифры на грани нечётного ряда; ▽ сверху — контакт 1 слева вверху, 2 под ним",
     "body": {"extra": 3.9, "h": 5.0, "len": 7.3, "win": (1.0, 1.4)}},
    # Micro-Fit plugs — 43020: catalogue sheet MX01 F-126 (third angle): the view above the front view is the mating end
    # with the latch ramp at its top (back face, side view) and CIRCUIT 1 bottom-right; the front face moulds «1» at the
    # right. From the wire side, ramp up: 1 bottom-LEFT, the far row 1…n left to right (the mirror of 43025, as a mated
    # pair). 43640: SD-43640-001 — the front view (mating end down, ramp on the front face) has the LAST CIRCUIT «3» at the
    # left, the rear view the FIRST CIRCUIT «1»; from the wire side, ramp up: contact 1 on the LEFT (mirror of 43645).
    {"id": "molex-microfit-plug", "title": "Molex Micro-Fit 3.0 (вилка)", "pattern": r"^43020-?(\d{2})0\d$", "pitch": 3.0, "rows": 2, "order": "row", "start": "BL", "latch": "lever", "color": BLACK, "erp_only": True, "conf": "high",
     "src": "Molex 43020, лист каталога MX01 F-126", "note": "по листу Molex 43020: со стыковки упором защёлки вверх CIRCUIT 1 внизу справа; со стороны проводов — 1 внизу слева"},
    {"id": "molex-microfit-plug-1r", "title": "Molex Micro-Fit 3.0 (вилка, 1 ряд)", "pattern": r"^43640-?0?(\d{1,2})0\d$", "pitch": 3.0, "rows": 1, "order": "row", "start": "TL", "latch": "lever", "color": BLACK, "erp_only": True, "conf": "high",
     "src": "Molex SD-43640-001", "note": "по чертежу Molex SD-43640-001: на лицевой грани с упором защёлки последний контакт слева, «1» справа; со стороны проводов упором вверх — контакт 1 слева"},
    # CLIK-Mate 1.5 502578 — Molex 5025780000-SD rev. A (datasheet.lcsc.com copy of 5025780600, read 16.10.2026): the front
    # view (mating end) has CIRCUIT 1 at the right; the rear view — the wire end with the moulded CIRCUIT 1 MARK ▽, trade
    # mark and cavity numbers — shows the latch lever on top and CIRCUIT 1 at the LEFT (as CLIK-Mate 2.0). Was TR (mirrored).
    # Orders: 502578 + count 02…15 + colour 00…09, 11, 14, 16 (sheet 4).
    {"id": "molex-clikmate", "title": "Molex CLIK-Mate 1,5", "pattern": r"^502578-?(\d{2})(?:0\d|1[146])$", "max": 15, "pitch": 1.5, "rows": 1, "order": "row", "start": "TL", "latch": "ramp", "color": IVORY, "erp_only": True, "conf": "high",
     "src": "Molex 5025780000-SD", "note": "по чертежу Molex 5025780000-SD: на торце проводов (метка ▽, номера гнёзд) защёлкой вверх контакт 1 слева"},
    # Pico-Clasp 501189 (2 rows) — Molex 5011890000-SD rev. E (datasheet.lcsc.com copy of 5011894010, read 16.10.2026): the
    # wire-end view (CIRCUIT 1 / 2 indicator marks, trade mark) has the lock on top, circuit 1 at the top-RIGHT, 2 under
    # it, N at the bottom-left — the columns of two run right to left. Orders 501189-nn10 (natural) / -nn11 (black), nn = 20, 30, 40, 50.
    {"id": "molex-picoclasp", "title": "Molex Pico-Clasp (2 ряда)", "pattern": r"^501189-?(\d{2})1[01]$", "pitch": 1.0, "rows": 2, "order": "col", "start": "TR", "latch": "ramp", "color": IVORY, "color_by": ((r"11$", BLACK),), "erp_only": True, "conf": "high",
     "src": "Molex 5011890000-SD", "note": "по чертежу Molex 5011890000-SD: на торце проводов защёлкой вверх 1 справа вверху, 2 под ней, колонки справа налево"},
    # Pico-Clasp 501330 (1 row) — Molex 5013300000-SD rev. D (datasheet.lcsc.com copy of 5013300800, read 16.10.2026): the
    # wire-end view (CIRCUIT 1 INDICATOR MARK, trade mark, cavity numbers) sits under the top view with the lock bump, so
    # lock up, CIRCUIT 1 at the LEFT, CIRCUIT N at the right; the mating end (small square entries) is the right-hand view.
    # Was TR (mirrored). Orders 501330-nn00 (natural) / -nn31 (black).
    {"id": "molex-picoclasp-1r", "title": "Molex Pico-Clasp", "pattern": r"^501330-?0?(\d{1,2})(?:00|31)$", "pitch": 1.0, "rows": 1, "order": "row", "start": "TL", "latch": "ramp", "color": IVORY, "color_by": ((r"31$", BLACK),), "erp_only": True, "conf": "high",
     "src": "Molex 5013300000-SD", "note": "по чертежу Molex 5013300000-SD: на торце проводов (метка CIRCUIT 1, номера гнёзд) защёлкой вверх контакт 1 слева"},
    {"id": "molex-milligrid", "title": "Molex Milli-Grid 2,0", "pattern": r"^51110-?(\d{2})[56][01]$", "pitch": 2.0, "rows": 2, "order": "col", "start": "TL", "latch": "friction", "color": BLACK, "erp_only": True, "conf": "low"},
    # Hirose drawings (third-angle; copies of the HRS PDFs from datasheet.lcsc.com — hirose.com blocks downloads): the
    # top view shows the mating face (square pin entries), the front view has the wire end DOWN, the view under it is the
    # wire end, the lowest view is the back face turned over. «CAVITY No.» there is the mould cavity mark, not a contact.
    # DF13 — EDC3-083675: «POLARIZATION MARKING» ▽ and «MARKING ON EVERY FIVE CONTACTS» on the front face count
    # contacts from the left; the locks (flag 1) sit on the BACK face -> from the wire side, locks up, contact 1 RIGHT.
    {"id": "hirose-df13", "title": "Hirose DF13", "pattern": r"^DF13-(\d+)S-1[.,]25C", "pitch": 1.25, "rows": 1, "order": "row", "start": "TR", "latch": "ramp", "color": IVORY, "erp_only": True, "conf": "high",
     "src": "Hirose EDC3-083675 (DF13-*S-1.25C)", "note": "по чертежу Hirose DF13: защёлки сверху, метка ▽ снизу — контакт 1 справа"},
    # DF14 — EDC3-160306-01: «CONTACT NO. 1» ▽ at the left of the front face, and the lock (flag 2) is on that same front
    # face -> from the wire side, lock up, contact 1 LEFT.
    {"id": "hirose-df14", "title": "Hirose DF14", "pattern": r"^DF14-(\d+)S-1[.,]25C", "pitch": 1.25, "rows": 1, "order": "row", "start": "TL", "latch": "ramp", "color": IVORY, "erp_only": True, "conf": "high",
     "src": "Hirose EDC3-160306-01 (DF14-*S-1.25C)", "note": "по чертежу Hirose DF14: защёлка и метка ▽ сверху — контакт 1 слева"},
    # DF11 — EDC-162389-00-01: top view «Contact No.1» in the front row, «Contact No.2» behind it; the front face carries
    # the polarizing ▽, a moulded «1» and «number of contacts minus 1», the back face «2» and «number of contacts». No
    # latch. From the wire side with the ▽ face up: 1 top-left, 2 under it, odd numbers along the top row.
    # A = B + 4.0 (B = (columns − 1)·2), 5.0 thick, 7.3 long.
    {"id": "hirose-df11", "title": "Hirose DF11 (2 ряда)", "pattern": r"^DF11-(\d+)DS", "pitch": 2.0, "rows": 2, "order": "col", "start": "TL", "latch": "mark", "color": IVORY, "erp_only": True, "conf": "high",
     "src": "Hirose EDC-162389-00-01 (DF11-*DS-2C)", "note": "по чертежу Hirose DF11: метка ▽ и цифра 1 сверху — контакт 1 вверху слева, чётные снизу",
     "body": {"extra": 4.0, "h": 5.0, "len": 7.3, "win": (1.2, 1.2)}},
    # FI-X — the only maker drawing reachable is JAE SJ111287 (FI-X30HL-B, via datasheet.lcsc.com; jae.com 404s): it
    # shows the housing and lock levers but no contact numbers, so the numbering stays unchecked.
    {"id": "jae-fix", "title": "JAE FI-X", "pattern": r"^FI-X(\d+)H", "pitch": 1.0, "rows": 1, "order": "row", "start": "TL", "latch": "ramp", "color": "#b9c0c6", "erp_only": True, "conf": "low",
     "note": "на чертеже JAE SJ111287 (FI-X30HL-B) номеров контактов нет — сверить по детали"},
    # D-Sub: two staggered rows; the standard numbering is mirrored between the socket and the pin side.
    {"id": "dsub", "title": "D-Sub", "pattern": r"^I?-?DS1033-0?(\d+)([FM])|^D-?SUB-?0?(\d+)([FM])|^D[BE]-?0?(9|15|25)([FMPS])", "kind": "dsub", "count_groups": (1, 3, 5), "gender_groups": (2, 4, 6), "pitch": 2.77, "color": "#c9ccd0", "erp_only": True, "conf": "high", "src": "DIN 41652 / IEC 60807-3", "note": "по стандарту D-Sub (номера отлиты на изоляторе): со стороны пайки у вилки контакт 1 справа в широком ряду, у розетки — слева; на чертеже Connfly C-DS1033 номеров нет"},
    {"id": "deutsch-dt", "title": "Deutsch DT", "pattern": r"^DT0([46])-(\d+)([PS])", "kind": "dt", "count_group": 2, "pitch": 4.0, "rows": 2, "order": "row", "start": "TL", "snake": True, "latch": "lever", "color": "#8d9297", "cavity": "round", "erp_only": True, "conf": "low", "note": "нумерация по кругу; сверить с цифрами на корпусе"},
    {"id": "te-superseal", "title": "TE Superseal 1.5", "pattern": r"^(28210[4-8]|28208[0789]|282090)-1$", "table": {"282104": 2, "282105": 3, "282106": 4, "282107": 5, "282108": 6, "282080": 2, "282087": 3, "282088": 4, "282089": 5, "282090": 6}, "pitch": 3.5, "rows": 1, "order": "row", "start": "TL", "latch": "lever", "color": BLACK, "cavity": "round", "erp_only": True, "conf": "low"},
    {"id": "aviation-gx", "title": "Авиационный GX", "pattern": r"^GX(12|16|20)M?-(\d+)", "kind": "round", "count_group": 2, "round_auto": True, "color": "#b9c0c6", "erp_only": True, "conf": "low",
     "note": "чертежа производителя для GX12 нет; каталоги GX16/GX20 дают раскладку без номеров и без стороны вида — нумерация по цифрам на изоляторе"},
    {"id": "dc-barrel", "title": "Штекер питания 5,5×2,1", "pattern": r"^DC-?5[.,]5", "kind": "barrel", "fixed": 2, "rows": 1, "color": BLACK, "labels": ("центр", "корпус"), "erp_only": True, "conf": "high"},
    {"id": "lug", "title": "Наконечник", "pattern": r"^(НКИ|НВИ|ПМ|РПИ|РППИ|НШВИ)", "kind": "lug", "conf": "high"},
    {"id": "banana", "title": "Штекер банан 4 мм", "pattern": r"^(BANANA|БАНАН)", "kind": "banana", "color": "#d7322d", "conf": "high"},
) + CATALOG_FAMILIES  # series added from the makers' catalogues (connector_catalog_data.py, 12.10.2026)


# Roof feature of the housing (owner, 17.10.2026: «сверху всегда замок или прорези, по детали»): what the top face of the
# real part carries, so the top view tells the side. `kind`: "latch" — lock lever/ramp/arms, "slots" — lance windows or
# polarising ribs; `at`: "center" (latch in the middle), "ends" (lock arms at both ends), "contacts" (a slot per contact),
# "keys" (polarising buttons/ribs, position approximate); `src` — the maker's sheet the feature was read from.
# A series with no sourced feature has NO roof (None): the drawing shows a plain top, never a guessed one.
JST_SHEET = "JST {0}, https://www.jst-mfg.com/product/pdf/eng/{0}.pdf"
LCSC_SHEET = "https://datasheet.lcsc.com/datasheet/pdf/{}.pdf?productCode={}"
DF1B_CAT_SRC = "Hirose DF1B, каталог 01.07.2020, {}"
ROOF_EXPLICIT: dict[str, dict[str, str]] = {
    # Connfly Dupont 2,54 / 2,0 (DS1071, DS1071-01): the Connfly sheet draws the housing symmetric, the real part carries a
    # lance window over every contact on the top face and the circuit-1 arrow at the left end (owner, 17.10.2026, by the part).
    "connfly-ds1071": {"kind": "slots", "at": "contacts", "src": OWNER_PART},
    "connfly-ds1071-01": {"kind": "slots", "at": "contacts", "src": OWNER_PART},
    # eSUH / eSHD / eSHL / eSHJ «Housing» (read 17.10.2026 via Web Archive): the front face of the housing carries a lance
    # window at every contact and the ▽ circuit-1 mark, no latch (like SH).
    "jst-suh": {"kind": "slots", "at": "contacts", "src": JST_SHEET.format("eSUH") + " (с. 2, Housing)"},
    "jst-shd": {"kind": "slots", "at": "contacts", "src": JST_SHEET.format("eSHD") + " (с. 2, Housing)"},
    "jst-shl": {"kind": "slots", "at": "contacts", "src": JST_SHEET.format("eSHL") + " (с. 2, Socket housing)"},
    "jst-shj": {"kind": "slots", "at": "contacts", "src": JST_SHEET.format("eSHJ") + " (с. 3, Socket housing)"},
    # eSM p. 1 «Arm lock mechanism» (picture and assembly side view) and p. 3 «Housing»: the receptacle SMR carries the
    # ribbed rocker lock lever in the middle of its top; the plug SMP the lock catch in the middle of its nose top. The
    # SMP arms on the row ends are the panel arms (p. 1 panel hole for the plug housing) — `body.wings`, not the roof.
    "jst-sm-r": {"kind": "latch", "at": "center", "src": JST_SHEET.format("eSM") + " (с. 1 рисунок и вид в сборе, с. 3 Receptacle housing: рычаг замка с рифлением по центру крыши)"},
    "jst-sm-p": {"kind": "latch", "at": "center", "src": JST_SHEET.format("eSM") + " (с. 3, Plug housing: упор замка по центру у стыковочного торца; плечи по торцам ряда — крепление в панель)"},
    # eJWPF p. 2 «Housing»: the tab housing (T) has the lock window in the middle of its shroud, the receptacle housing
    # (R) the lock lever on that face.
    "jst-jwpf": {"kind": "latch", "at": "center", "src": JST_SHEET.format("eJWPF") + " (с. 2, Tab housing: окно замка)"},
    "jst-jwpf-r": {"kind": "latch", "at": "center", "src": JST_SHEET.format("eJWPF") + " (с. 2, Receptacle housing: рычаг замка)"},
    # PicoBlade 51047 — Molex 510470000-SD note 4: a locking window in the shroud, one for 2–3 circuits, two from 4 on.
    "molex-picoblade-plug": {"kind": "latch", "at": "center", "pair_from": 4,
                             "src": "Molex 510470000-SD, л. 1, прим. 4 «LOCKING WINDOW» (2–3 конт. — одно, от 4 — два)"},
    # ePHD «Socket housing»: the locks are hooks on the end walls (the ▽ face is only the face cue).
    "jst-phd": {"kind": "latch", "at": "ends", "src": JST_SHEET.format("ePHD") + " (с. 2, Socket housing: замки на торцах)"},
    # Zero-Hachi sheet 2 «LOCK SHAPE»: side lock for 2–10 circuits (11–20 have the two-sided lock).
    "molex-zerohachi": {"kind": "latch", "at": "ends", "src": "Molex 2147190000-SD, л. 2 «LOCK SHAPE», " + LCSC_SHEET.format("c7076ff0722df1ed5ce187d7673c4af7", "C7603638")},
    # Milli-Grid 151192 sheet 1: «LATCH» callout at the end of the front face, «single latch for 4 CKTS to 12 CKTS only».
    "molex-milligrid-151192": {"kind": "latch", "at": "ends", "src": "Molex 1511921001, л. 1 «LATCH», " + LCSC_SHEET.format("045b3c208df0ef4741b42924f908715d", "C3269195")},
    # Pico-Lock: the locks are arms at both ends of the housing (note read from the maker sheets, see connector_catalog_data.py).
    "molex-picolock-20": {"kind": "latch", "at": "ends", "src": "Molex 2053410000-SD, замки-плечи на торцах"},
    "molex-picolock-15": {"kind": "latch", "at": "ends", "src": "Molex 5040510000-SD, замки-плечи на торцах"},
    "molex-picolock-10": {"kind": "latch", "at": "ends", "src": "Molex SD-503764-001, замки-плечи на торцах"},
    # C-Grid III SD-90123: the face with the polarising buttons («90123») is the one that goes up.
    "molex-cgrid3-1r": {"kind": "slots", "at": "keys", "src": "Molex SD-90123, грань с кнопками поляризации"},
    # Second pass (17.10.2026, sheets read via Web Archive / datasheet.lcsc.com / chipdip): only what the sheet's text or
    # its picture of the housing shows. JST catalogue pages are cited by their first page (feature list + photo).
    "jst-el": {"kind": "latch", "at": "center", "src": JST_SHEET.format("eEL") + " (с. 1, «Durable arm lock mechanism», рычаг замка на розетке ELP)"},
    "jst-el-n": {"kind": "latch", "at": "center", "src": JST_SHEET.format("eEL") + " (с. 1, «Durable arm lock mechanism»)"},
    "jst-gvh": {"kind": "latch", "at": "center", "src": JST_SHEET.format("eGVH") + " (с. 1, «Secure locking structure», как у GVH side feed)"},
    "jst-lbt": {"kind": "latch", "at": "ends", "src": JST_SHEET.format("eLBT") + " (с. 1, фото вилки: петли замка на обоих торцах)"},
    "jst-pnd": {"kind": "latch", "at": "center", "src": JST_SHEET.format("ePND") + " (с. 1, «Secure lock mechanism», рычаг на вилке)"},
    "jst-sqs": {"kind": "latch", "at": "center", "src": JST_SHEET.format("eSQS") + " (с. 1, «Intelligent CPA»: ползунок-фиксатор на корпусе)"},
    "jst-vb": {"kind": "latch", "at": "ends", "src": JST_SHEET.format("eVB") + " (с. 1, рисунок розетки: скосы замка на торцах корпуса)"},
    "jst-ghd": {"kind": "latch", "at": "center", "src": JST_SHEET.format("eGHD") + " (с. 1, «Secure lock device»)"},
    "jst-znd": {"kind": "latch", "at": "center", "src": JST_SHEET.format("eZND") + " (с. 1, «Secure lock mechanism»)"},
    # JAE FI-R MB-0153-4 p. 1: «Mechanical lock», the 3D picture of the plug shows the lock arms at both ends; every FI-RE…HL
    # is the «L: Locking Latches» plug (JAE Board-to-Cable overview v2.2).
    "jae-fir": {"kind": "latch", "at": "ends", "src": "JAE MB-0153-4 (FI-R), с. 1, «Mechanical lock», " + LCSC_SHEET.format("c8110807488e21f4fc506aaa43b04ed7", "C570465")},
    # Hirose DF19 catalogue 01.01.2024, cover photo of the cable sockets: the metal shell carries lock tabs at both ends.
    "hirose-df19": {"kind": "latch", "at": "ends", "src": "Hirose DF19, каталог 01.01.2024, с. 1 (фото кабельных колодок: лапки замка на торцах), " + LCSC_SHEET.format("d0e6797f2ca3f8a48e9b5ce30acf807e", "C3268449")},
    # Connfly C-DS1073-XXXXX-E «Female Housing»: a lock tab with its window in the middle of the long face.
    "connfly-ds1073": {"kind": "latch", "at": "center", "src": "Connfly C-DS1073-XXXXX-E, «Female Housing» (https://www.connfly.com/static/upload/file/pdf/DS1073.pdf)"},
    # Molex part sheets / drawings: 2064610000-SD sheet 1 (lock lever on the top of the housing), 0511630400 («Positive Lock»,
    # photo with the lock tab in the middle), 0050579404 («Positive Latch», photo), 0349671005 (photo with the lock arm on top).
    "molex-microfit-plus": {"kind": "latch", "at": "center", "src": "Molex 2064610000-SD, л. 1 (рычаг замка сверху), https://static.chipdip.ru/lib/869/DOC034869295.pdf"},
    "molex-minilock": {"kind": "latch", "at": "center", "src": "Molex 0511630400, лист детали: «Positive Lock», https://static.chipdip.ru/lib/341/DOC012341229.pdf"},
    "molex-sl-1r": {"kind": "latch", "at": "center", "src": "Molex 0050579404, лист детали: «Positive Latch», https://pccomponents.com/datasheets/MOLEX-50579404.pdf"},
    "molex-mini50-s": {"kind": "latch", "at": "center", "src": "Molex 0349671005, лист детали (фото: рычаг замка сверху), https://cdn.eicom.ru/media/PDF/1030948.pdf"},
    # Pico-EZmate 78172-0002 part sheet picture: a lance cut-out at every cavity on the top face next to the ▽ mark.
    "molex-picoezmate": {"kind": "slots", "at": "contacts", "src": "Molex 78172-0002, лист детали (рисунок: окно под каждый контакт сверху), https://static.chipdip.ru/lib/211/DOC015211617.pdf"},
    # TE 174047-2 product photos (te.com.cn datasheet via Web Archive): the integral lock block in the middle of the top.
    "te-multilock-040": {"kind": "latch", "at": "center", "src": "TE 174047-2, лист изделия (фото: блок замка по центру, «外壳集成»), https://www.te.com.cn/chn-zh/product-174047-2.datasheet.pdf"},
    # KLS L-KLS1-2.50-XX-H drawing: a lance window per cavity on the long face (ZH type).
    "kls-2-50": {"kind": "slots", "at": "contacts", "src": "KLS L-KLS1-2.50-XX-H, чертёж, https://static.chipdip.ru/lib/526/DOC059526514.pdf"},
    # Hirose drawings from hirose.com API (read 17.10.2026, drawing_2d, third angle): DF61 EDC3-336116-04 (DF61-2S-2.2C(11)): the plan view
    # shows a lock lever along the middle of the face (side view: its hook), ▽ «Contact No.1 marking» on that face;
    # DF1B DEP EDC3-160056-01: the plan view shows the lock lever between two guide rails (side view: hook).
    "hirose-df61": {"kind": "latch", "at": "center", "src": "Hirose EDC3-336116-04 (DF61-2S-2.2C), https://www.hirose.com/api/v1/products/CL0666-5002-4-11/documents/drawing_2d/content (вид сверху: рычаг замка по центру грани, на виде сбоку — зацеп)"},
    "hirose-df1b-p2r": {"kind": "latch", "at": "center", "src": "Hirose EDC3-160056-01 (DF1B-*DEP-2.5RC), https://www.hirose.com/api/v1/products/CL0541-0366-9-00/documents/drawing_2d/content (вид сверху: рычаг замка между двумя направляющими, на виде сбоку — зацеп)"},
    # RJ45 8P8C: the latch tab on the plug body (IEC 60603-7).
    "rj45": {"kind": "latch", "at": "center", "src": "IEC 60603-7 (8P8C): защёлка на корпусе вилки"},
    # Third pass (17.10.2026, evening): every series that had no sourced roof. Hirose catalogue pages (third angle) show
    # the face with the «Contact No.1» mark as the front view: DF13 / DF20 / DF11 sockets and DF1B S / DS / ES / DES
    # draw a window over every contact on it (the photos «Contact inserted» show the contacts through them); the locks
    # of the in-line sockets are hooks on the back face, not on this face.
    "hirose-df13-2r": {"kind": "slots", "at": "contacts", "src": "Hirose DF13, каталог B242, Double Row Socket (вид спереди — грань с меткой ▽ Contact No.1: окно над каждым контактом)"},
    "hirose-df20": {"kind": "slots", "at": "contacts", "src": "Hirose DF20, каталог B266 (01.11.2016), Double Row Socket (вид спереди — грань с меткой Contact No.1: окно над каждым контактом; фото с контактами)"},
    "hirose-df11": {"kind": "slots", "at": "contacts", "src": "Hirose DF11, каталог B220 (01.06.2020), Crimping Socket (вид спереди — грань с Polarization Mark: окно над каждым столбцом; фото «Contact insertion status»)"},
    "hirose-df1b": {"kind": "slots", "at": "contacts", "src": DF1B_CAT_SRC.format("Single Row Socket (вид спереди — грань с меткой ① Contact No1.: окно над каждым контактом)")},
    "hirose-df1b-2r": {"kind": "slots", "at": "contacts", "src": DF1B_CAT_SRC.format("Double Row Socket (вид спереди — грань с меткой «1»: окно над каждым столбцом)")},
    "hirose-df1b-es": {"kind": "slots", "at": "contacts", "src": DF1B_CAT_SRC.format("Single Row In-line Socket (Full Lock Type) (вид спереди: окно над каждым контактом; зацеп замка — на обратной грани)")},
    "hirose-df1b-des": {"kind": "slots", "at": "contacts", "src": DF1B_CAT_SRC.format("Double Row In-line Socket (Full Lock Type) (вид спереди — грань с меткой Contact No1.: окно над каждым столбцом; зацеп — на обратной грани)")},
    # DF1BA (simple lock): the front face with the «Contact No1. mark» is smooth — two shallow recesses, no lock, no window
    # per contact; the panel arms sit on the end walls.
    "hirose-df1ba-p": {"kind": "none", "src": DF1B_CAT_SRC.format("Single Row In-line Plug (Simple Lock Type) (вид спереди: грань гладкая, две выемки; замок — плечи на торцах)")},
    "hirose-df1ba-p2r": {"kind": "none", "src": DF1B_CAT_SRC.format("Double Row In-line Plug (Simple Lock Type) (вид спереди: грань гладкая; замок — плечи на торцах)")},
    # JST eRCY p. 2 (and the p. 1 picture): SYR — a smooth top with «JST 1 2» moulded, no lock; SYP — open windows over the
    # two contacts on top.
    "jst-rcy": {"kind": "none", "src": JST_SHEET.format("eRCY") + " (с. 2, Receptacle housing: верх гладкий, отлито «JST 1 2», замка и окон нет)"},
    "jst-rcy-p": {"kind": "slots", "at": "contacts", "src": JST_SHEET.format("eRCY") + " (с. 1 рисунок и с. 2 Plug housing: сверху открытое окно над каждым контактом)"},
    # Molex SL dual row 22-55-2xx1 — SD-70450-0001-0026 (pdm_docs copy of 022552081_sd.pdf in the Web Archive): the face
    # with the ▽ CKT. NO. 1 has a window over every column. Milli-Grid 51110 — SD-51110-XX5X rev. B11 (511101051_sd.pdf):
    # the face with the ▽ «1ST CIRCUIT IDENTIFICATION» has a window over every column; the centre polarisation key and
    # the side latches are options (-XX51 / -XX50, sheet 2), the windows are on every housing.
    "molex-sl-2r": {"kind": "slots", "at": "contacts", "src": "Molex SD-70450-0001-0026 (022552081_sd.pdf, Web Archive): грань с меткой ▽ CKT. NO. 1 — окно над каждым столбцом"},
    "molex-milligrid": {"kind": "slots", "at": "contacts", "src": "Molex SD-51110-XX5X rev. B11 (511101051_sd.pdf): грань с меткой ▽ 1ST CIRCUIT — окно над каждым столбцом; ключ и боковые защёлки — по исполнению"},
    # Mini50 unsealed 34791 — SD-34791-001 rev. L7 (347910040_sd.pdf, Web Archive), sheets 1–3: the lock in the middle of
    # the top on every housing (the «bridge» option only adds a guard over it).
    "molex-mini50-u": {"kind": "latch", "at": "center", "src": "Molex SD-34791-001 rev. L7, л. 1–3 (замок по центру верхней грани; «мост» — защита над ним), 347910040_sd.pdf"},
    # Board-in 35023 — SD-35023-001: the top face carries the entry of a straight contact 35044 over every cavity, no lock.
    "molex-boardin": {"kind": "slots", "at": "contacts", "src": "Molex SD-35023-001 (350230004_sd.pdf): верхняя грань — вход прямого контакта 35044 над каждым гнездом, замка нет"},
    # JAE FI-S SJ031451: the top view is the face with a window over every contact (friction lock, no latch).
    "jae-fis": {"kind": "slots", "at": "contacts", "src": "JAE SJ031451 (FI-S※※S Housing): вид сверху — окно над каждым контактом, замка нет"},
    # FI-X: SJ033913 (FI-X※※H) — a window over every contact on top, no lock; the HL housings are in ROOF_BY_ARTICLE.
    "jae-fix": {"kind": "slots", "at": "contacts", "src": "JAE SJ033913 (FI-X※※H Housing): вид сверху — окно над каждым контактом, замка нет"},
    # Connfly drawings (connfly.com): DS1070 rev. D sheet 2 «Female Housing» — smooth faces with a step, no lock;
    # DS1072 rev. D and DS1074 rev. D «Female Housing» — the side view has the lock ramp on top at the mating end, the
    # front view a raised strip along the whole top.
    "connfly-ds1070": {"kind": "none", "src": "Connfly C-DS1070-XXXXXX-D, л. 2 «Female Housing»: грани гладкие, ступенька; замка и окон нет (https://www.connfly.com/static/upload/file/pdf/DS1070.pdf)"},
    "connfly-ds1072": {"kind": "latch", "at": "center", "src": "Connfly C-DS1072-XXXXX-D «Female Housing»: рампа замка сверху у стыковочного торца, по всей ширине (https://www.connfly.com/static/upload/file/pdf/DS1072.pdf)"},
    "connfly-ds1074": {"kind": "latch", "at": "center", "src": "Connfly C-DS1074-XXXXX-D «Female Housing»: рампа замка сверху у стыковочного торца, по всей ширине (https://www.connfly.com/static/upload/file/pdf/DS1074.pdf)"},
    # Hsuan Mao (definum.ru copies): H2011-XXPW0000R — the lock block in the middle of the top (dimension C), as JST PA;
    # H5080-XXPXW000 — «Y: with locking rib» on the top face, «N: without» (ROOF_BY_ARTICLE).
    "hsm-hc": {"kind": "latch", "at": "center", "src": "Hsuan Mao H2011-XXPW0000R rev. A: блок замка по центру верхней грани (размер C)"},
    "hsm-mx508": {"kind": "latch", "at": "center", "src": "Hsuan Mao H5080-XXPXW000 rev. A: Body Option Y — ребро замка (locking rib) на верхней грани"},
    # Samtec ISD2 (samtec.com S2SD / ISD2 sheet F-226): the face with the ▽ has a window over every column; the -M metal
    # retention latch and the -S screws are on the end walls.
    "samtec-isd2": {"kind": "slots", "at": "contacts", "src": "Samtec ISD2 / S2SD, лист F-226 (вид грани с меткой ▽: окно над каждым столбцом; защёлка -M и винты -S — на торцах)"},
    # Deutsch DT (TE drawings DT04-2P/-4P/-6P «Receptacle», DT06-2S/-4S/-08SA/-12S «Plug»): the plug's latch arm runs along
    # the middle of its top, the receptacle's shroud carries the latch ramp in the middle of its top.
    "deutsch-dt": {"kind": "latch", "at": "center", "src": "Deutsch DT04-2P rev. C / DT04-4P rev. B (рампа замка по центру верха), DT06-2S rev. C / DT06-4S rev. B1 (рычаг замка по центру верха)"},
    # TE Superseal 1.5 — C-282106 rev. B4 «4 positions tab ass'y» (the lock rail on top, section B-B) and C-282088 rev. F4
    # «4 positions plug ass'y» (the lock lever in the middle of the top).
    "te-superseal": {"kind": "latch", "at": "center", "src": "TE C-282106 rev. B4 (Tab ass'y: направляющая замка по центру верха, сечение B-B), C-282088 rev. F4 (Plug ass'y: рычаг замка по центру верха)"},
    # USB 2.0 Specification fig. 6-9: the Series A plug shell has two latch windows in its face; fig. 6-7: the receptacle
    # shell has two spring latches there.
    "usb-a": {"kind": "latch", "at": "pair", "src": "USB 2.0 Specification, рис. 6-9 (вилка A: два окна фиксации в оболочке) и рис. 6-7 (розетка A: два пружинных фиксатора)"},
    # Amass XT60 / XT60H product specifications 2021V1 (views of -M and -F): smooth housings, no lock, no windows.
    "amass-xt": {"kind": "none", "src": "Amass XT60 / XT60H, Product Specification 2021V1 (виды -M и -F: корпус гладкий, замка и окон нет)"},
}
# Roof features that differ inside one family by the part number: (family id, article regex, roof); first match wins.
# "kind": "none" — the sheet shows a plain top (no lock, no window), recorded with its source.
ROOF_BY_ARTICLE: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("jae-fix", r"^FI-X\d+HL", {"kind": "latch", "at": "ends", "src": "JAE SJ037012 rev. 3 (FI-X30HL Housing): рычаги замка (LOCK LEVER, нерж. сталь) на обоих торцах"}),
    ("hsm-mx508", r"^(?:HSM)?H5080-\d{2}PN", {"kind": "none", "src": "Hsuan Mao H5080-XXPXW000 rev. A: Body Option N — без ребра замка, верх гладкий"}),
)
# Series still without a sourced roof, and why no maker document exists (the frontend list UNSOURCED mirrors this):
# - jst-ra: eRA.pdf is gone from jst-mfg.com (404) and the Web Archive holds no copy (CDX empty, 17.10.2026);
# - amass-qs8: QS8 is not in the Amass catalogues (china-amass.com downloads 2026) — the maker of the housing is unknown;
# - jr-servo: the shop's JR / Futaba servo housings have no maker and no part number.


def _roof_entry(family: Mapping[str, Any], article: str) -> dict[str, str] | None:
    for family_id, pattern, entry in ROOF_BY_ARTICLE:
        if family_id == family["id"] and re.search(pattern, article, flags=re.I):
            return entry
    return ROOF_EXPLICIT.get(family["id"])


def roof_plain_of(family: Mapping[str, Any], article: str = "") -> str:
    """The source of a sheet that shows a plain top (no lock, no window) for this housing, or ""."""
    entry = _roof_entry(family, article)
    return entry["src"] if entry and entry.get("kind") == "none" else ""


def roof_of(family: Mapping[str, Any], count: int = 0, article: str = "") -> dict[str, str] | None:
    """The sourced roof feature of a family, or None. Besides the explicit tables (ROOF_BY_ARTICLE, ROOF_EXPLICIT; an
    entry of kind "none" is a sourced plain top — no roof, see roof_plain_of), a series whose drawing was read (high
    confidence + a named source) keeps what that drawing shows: lock lever/ramp -> latch in the middle; contact-lance
    windows face (JST SH/PH/ZH, Connfly DS1066, Mini-SPOX, PicoBlade) -> a slot per contact. `pair_from`: from that many
    contacts the sheet shows two lock features instead of one (at "pair")."""
    explicit = _roof_entry(family, article)
    if explicit and explicit.get("kind") == "none":
        return None
    if explicit:
        roof = {key: value for key, value in explicit.items() if key != "pair_from"}
        if count and count >= int(explicit.get("pair_from") or 10**6):
            roof["at"] = "pair"
        return roof
    src = family.get("src")
    if not src or family.get("conf") != "high":
        return None
    if family.get("model") == "slots" or family.get("latch") == "windows":
        return {"kind": "slots", "at": "contacts", "src": src}
    if family.get("latch") in {"lever", "ramp"}:
        return {"kind": "latch", "at": "center", "src": src}
    return None


# Mating side of the wire-to-wire pairs (owner, 17.10.2026: «а почему у тебя розетка и вилка одинаково выглядит»): what
# the maker's sheet shows for each housing of a pair, so the two halves of one series are drawn apart.
# `gender` — the maker's name of the housing: "receptacle" (розетка) or "plug" (вилка). The name does not say which
# contacts it takes (JST: the receptacle SMR takes the pins; Molex: the receptacle 43025 takes the sockets), hence
# `contacts` — "pin" (штыри) or "socket" (гнёзда). `shell` — "outer": its shroud takes the other housing in; "inner":
# it goes into the other one. `lock` — "arm": the flexible lock arm / lever; "catch": the rigid ramp or hook the arm
# grips; "window": the opening the other housing's lock snaps into; absent — the sheet shows no lock between the two.
# Where the lock sits (middle, ends) is the roof's `at` (ROOF_EXPLICIT / roof_of). `src` — the sheet it was read from.
def _mate(gender: str, contacts: str, shell: str, lock: str, src: str) -> dict[str, str]:
    mate = {"gender": gender, "contacts": contacts, "shell": shell, "lock": lock, "src": src}
    return {key: value for key, value in mate.items() if value}


ESM_3 = JST_SHEET.format("eSM") + " (с. 3, Housing)"
ERCY_2 = JST_SHEET.format("eRCY") + " (с. 2, Housing: SYP входит в SYR, замка на листе нет)"
EEL_2 = JST_SHEET.format("eEL") + " (с. 2–3: Receptacle housing for pin contact, Plug housing for socket contact)"
EJWPF_2 = JST_SHEET.format("eJWPF") + " (с. 2: Tab housing for Tab contact, Receptacle housing for Receptacle contact)"
DF1B_CAT = "Hirose DF1B, каталог 01.07.2020 (штыревой корпус на провод — кожух 7 мм с рычагом, гнездовой — с зацепом)"
MATING: dict[str, dict[str, str]] = {
    # eSM p. 3: «Receptacle housing (for pin contact)» SMR — the hood (B = A + 5.5, 7.5 thick) around the pins, with the
    # rocker lock lever on top (p. 1); «Plug housing (for socket contact)» SMP — its nose goes into the hood, the lever
    # hooks the catch on the nose top.
    "jst-sm-r": _mate("receptacle", "pin", "outer", "arm", ESM_3 + ", с. 1 (рычаг замка на вилке SMR)"),
    "jst-sm-p": _mate("plug", "socket", "inner", "catch", ESM_3 + ", с. 1 (упор замка на розетке SMP, плечи — крепление в панель)"),
    # eRCY p. 2: SYR (for pin contact) 6.7 high with a 5.9 opening, SYP (for socket contact) 5.3 at the mating part.
    "jst-rcy": _mate("receptacle", "pin", "outer", "", ERCY_2),
    "jst-rcy-p": _mate("plug", "socket", "inner", "", ERCY_2),
    # eJWPF p. 2: the tab housing T (tab = pin contacts) is the shroud with the lock window; the receptacle housing R
    # (receptacle = socket contacts) goes in with its lock lever (the photo on p. 1 shows R entering T).
    "jst-jwpf": _mate("", "pin", "outer", "window", EJWPF_2),
    "jst-jwpf-r": _mate("receptacle", "socket", "inner", "arm", EJWPF_2),
    # PicoBlade 510470000-SD: «WTW PLUG HOUSING», applicable plug terminal 501258, the shroud with the locking windows;
    # it mates with 51021 (receptacle terminals 50079 / 50058; MX01 A-119 «Friction locks» — the bumps on its top).
    "molex-picoblade-plug": _mate("plug", "pin", "outer", "window", "Molex 510470000-SD, л. 1–2"),
    "molex-picoblade": _mate("receptacle", "socket", "inner", "", "Molex 510470000-SD, л. 2 «MATE WITH 51021»; Molex MX01 A-119"),
    # Micro-Fit 3.0: the receptacles 43025 / 43645 take the female terminals 43030 / 46235 and carry the latch arm
    # (SDA-43025, SDA-43645); the plugs 43020 / 43640 take the male terminals 43031, their shroud has the latch ramp on
    # top (MX01 F-126 photo, SD-43640-001 «MALE TERMINAL»).
    "molex-microfit": _mate("receptacle", "socket", "inner", "arm", "Molex SDA-43025 («RECEPTACLE … FEMALE TERMINAL SERIES 43030 OR 46235»)"),
    "molex-microfit-1r": _mate("receptacle", "socket", "inner", "arm", "Molex SDA-43645 («RECEPTACLE ACCEPTS MOLEX MICRO FIT FEMALE TERMINAL»)"),
    "molex-microfit-plug": _mate("plug", "pin", "outer", "catch", "Molex 43020, лист каталога MX01 F-126 («Mates With 43025, Use With 43031», фото: упор защёлки на кожухе)"),
    "molex-microfit-plug-1r": _mate("plug", "pin", "outer", "catch", "Molex SD-43640-001 («PLUG … MALE TERMINAL», упор защёлки на лицевой грани)"),
    # Nano-Fit: 2002771000-SD / 2014441000-SD «PLUG HOUSING», terminal 201447, mated view — the receptacle goes into the
    # plug and its lever grips the plug's catch frame; SD-105307-100 / SD-105308-100: receptacle, terminal 105300.
    "molex-nanofit-plug-1r": _mate("plug", "pin", "outer", "catch", "Molex 2002771000-SD, л. 1–2 (терминал 201447, ответная 105307)"),
    "molex-nanofit-plug": _mate("plug", "pin", "outer", "catch", "Molex 2014441000-SD (терминал 201447, ответная 105308)"),
    "molex-nanofit-1r": _mate("receptacle", "socket", "inner", "arm", "Molex SD-105307-100 (терминал 105300); 2002771000-SD, л. 2 — вид в сборе"),
    "molex-nanofit": _mate("receptacle", "socket", "inner", "arm", "Molex SD-105308-100 (терминал 105300); 2014441000-SD — вид в сборе"),
    # Micro-Lock Plus 1.25 W/W: 2145250000-SD «PLUG HOUSING», plug terminal 2145241000, the shroud with the lock window
    # (sheet 2 mated view); 2145260000-SD «RECEPTACLE HOUSING», terminal 5054311000, its lock goes into that window.
    "molex-microlock-125-p": _mate("plug", "pin", "outer", "window", "Molex 2145250000-SD, л. 1–3"),
    "molex-microlock-125-s": _mate("receptacle", "socket", "inner", "arm", "Molex 2145260000-SD; 2145250000-SD, л. 2 — вид в сборе"),
    # Hirose DF1B: the in-line plugs EP / DEP take the pin contacts in a 7 mm shroud with the lock lever, the in-line
    # sockets ES / DES the socket contacts with the lock hook (on the back face — not on the roof); S / DS go onto the
    # DF1BZ headers; DF1BA (simple lock) — the panel plug without the lever.
    "hirose-df1b-p": _mate("plug", "pin", "outer", "arm", DF1B_CAT),
    "hirose-df1b-p2r": _mate("plug", "pin", "outer", "arm", DF1B_CAT),
    "hirose-df1ba-p": _mate("plug", "pin", "outer", "", DF1B_CAT),
    "hirose-df1ba-p2r": _mate("plug", "pin", "outer", "", DF1B_CAT),
    "hirose-df1b-es": _mate("receptacle", "socket", "inner", "catch", DF1B_CAT),
    "hirose-df1b-des": _mate("receptacle", "socket", "inner", "catch", DF1B_CAT),
    "hirose-df1b": _mate("receptacle", "socket", "inner", "", DF1B_CAT),
    "hirose-df1b-2r": _mate("receptacle", "socket", "inner", "", DF1B_CAT),
}
# Families that hold both halves of a pair under one pattern: (family id, article regex, entry); first match wins.
MATING_BY_ARTICLE: tuple[tuple[str, str, dict[str, str]], ...] = (
    # eEL p. 2–3: ELR «Receptacle housing (for pin contact)» — the shroud with the lock catch (and the panel arms);
    # ELP «Plug housing (for socket contact)» — goes in, the lock lever on its face.
    ("jst-el", r"^ELR", _mate("receptacle", "pin", "outer", "catch", EEL_2)),
    ("jst-el", r"^ELP", _mate("plug", "socket", "inner", "arm", EEL_2)),
    ("jst-el-n", r"^ELR", _mate("receptacle", "pin", "outer", "catch", EEL_2)),
    ("jst-el-n", r"^ELP", _mate("plug", "socket", "inner", "arm", EEL_2)),
    # The shop's own servo housings: «Корпус под гнёзда JR-T-Female» / «Корпус под штыри JR-T-Male» (no maker sheet,
    # so no shell or lock).
    ("jr-servo", r"FEMALE$", _mate("", "socket", "", "", "каталог ERP: JR-H-Female — под гнёзда JR-T-Female (Pololu 1924)")),
    ("jr-servo", r"MALE$", _mate("", "pin", "", "", "каталог ERP: JR-H-Male — под штыри JR-T-Male (Pololu 1925)")),
    # Deutsch DT (TE/Deutsch drawings): DT04-xP «x way receptacle» takes the pins, its shroud takes the plug in («plug
    # inserts into recept.», DT06-2S) and carries the latch ramp; DT06-xS «x way plug» takes the sockets, its latch arm
    # grips that ramp.
    ("deutsch-dt", r"^DT04", _mate("receptacle", "pin", "outer", "catch", "Deutsch DT04-2P rev. C, DT04-4P rev. B («2/4 WAY RECEPTACLE», штыри; DT06-2S: «PLUG INSERTS INTO RECEPT.»)")),
    ("deutsch-dt", r"^DT06", _mate("plug", "socket", "inner", "arm", "Deutsch DT06-2S rev. C («2 WAY PLUG … MATES WITH THE DT04-2P RECEPTACLE»), DT06-4S rev. B1")),
    # TE Superseal 1.5: C-282104 / C-282106 «tab ass'y» — the tab (pin) housing, its shroud with the lock rail takes the
    # plug; C-282080 / C-282088 «plug ass'y» — receptacle contacts, goes in with the lock lever.
    ("te-superseal", r"^28210", _mate("", "pin", "outer", "catch", "TE C-282104 rev. B2, C-282106 rev. B4 («Superseal 1.5 … tab ass'y», направляющая замка сверху)")),
    ("te-superseal", r"^2820[89]", _mate("plug", "socket", "inner", "arm", "TE C-282080 rev. D5, C-282088 rev. F4 («Superseal 1.5 … plug ass'y», рычаг замка, уплотнение снаружи)")),
    # Amass XT60 / XT60H Product Specification 2021V1: -M (公头) — the pins inside the housing; -F (母头) — the sockets in
    # a narrower nose that goes into it. No lock.
    ("amass-xt", r"-M$", _mate("", "pin", "outer", "", "Amass XT60 / XT60H, Product Specification 2021V1 (вид XT60-M: штыри внутри корпуса)")),
    ("amass-xt", r"-F$", _mate("", "socket", "inner", "", "Amass XT60 / XT60H, Product Specification 2021V1 (вид XT60-F: гнёзда в узкой части корпуса, входит в -M)")),
    # Weipu SA8 (WEIPU Circular Connector Catalog 2026.6, p. P05-03): SA810 «cable connector» (Ø12.6, the push-pull
    # sleeve over the mate) mates with SA811 in-line, SA812 rear-nut, SA815 front-nut (Ø9.4 front part); P — male
    # contact, S — female contact, in either housing.
    ("weipu-sa8", r"^SA810/P", _mate("plug", "pin", "outer", "", "WEIPU Circular Connector Catalog 2026.6, с. P05-03 (SA810 Cable connector, P — Male contact)")),
    ("weipu-sa8", r"^SA810/S", _mate("plug", "socket", "outer", "", "WEIPU Circular Connector Catalog 2026.6, с. P05-03 (SA810 Cable connector, S — Female contact)")),
    ("weipu-sa8", r"^SA81[125]/P", _mate("receptacle", "pin", "inner", "", "WEIPU Circular Connector Catalog 2026.6, с. P05-03 (SA811/812/815 — Mate with SA810, P — Male contact)")),
    ("weipu-sa8", r"^SA81[125]/S", _mate("receptacle", "socket", "inner", "", "WEIPU Circular Connector Catalog 2026.6, с. P05-03 (SA811/812/815 — Mate with SA810, S — Female contact)")),
    # CNLINKO LP-12 (Linko drawing LP-12-xxxPE-02-0xxx, part-number rules; catalogue p. 17–18): C — male pins, J — female;
    # PE «Plug» (the male plug carries the snap lock, catalogue «reinforce lock»), SX «Receptacle», PP «Floating connector».
    ("cnlinko-lp12", r"^LP-12-C0?\dPE", _mate("plug", "pin", "", "arm", "Linko LP-12-xxxPE-02-0xxx (C = male pin, PE = Plug); каталог CNLINKO LP-12, с. 17 (Male plug, замок на вилке)")),
    ("cnlinko-lp12", r"^LP-12-J0?\dSX", _mate("receptacle", "socket", "", "", "Linko LP-12-xxxPE-02-0xxx, правила номера (J = female pin, SX = Receptacle); каталог CNLINKO LP-12, с. 18")),
    ("cnlinko-lp12", r"^LP-12-J0?\dPP", _mate("", "socket", "", "", "Linko LP-12-xxxPE-02-0xxx, правила номера (J = female pin, PP = Floating connector); каталог CNLINKO LP-12, с. 18")),
)
# Pairs whose two halves differ only by the part number's gender letter: contacts from it, by the family's standard.
MATING_BY_MALE: dict[str, str] = {
    "dsub": "DIN 41652 / IEC 60807-3: вилка (M) — штыри, розетка (F) — гнёзда",
}


def mate_of(family: Mapping[str, Any], article: str, male: bool | None) -> dict[str, str] | None:
    """The mating side of a housing (MATING), or None when no sheet says it. A family whose part number carries the
    gender (D-Sub) gets the contact kind only; DT, Superseal, Amass XT, Weipu SA8 and LP-12 read it from the part
    number (MATING_BY_ARTICLE)."""
    for family_id, pattern, entry in MATING_BY_ARTICLE:
        if family_id == family["id"] and re.search(pattern, article, flags=re.I):
            return dict(entry)
    if family["id"] in MATING:
        return dict(MATING[family["id"]])
    if family["id"] in MATING_BY_MALE and male is not None:
        return _mate("plug" if male else "receptacle", "pin" if male else "socket", "", "", MATING_BY_MALE[family["id"]])
    return None


# The wire-to-wire pairs the catalogue holds both halves of, one sample article per half: (series, one half, other half).
# Owner, 17.10.2026 («давай по-русски»): the Russian name follows the contacts, not the maker's English «plug» /
# «receptacle» — вилка is the half with pins (штыри), розетка the half with sockets (гнёзда), in every series. The one
# place that knows it: titles are re-worded from `contacts` here, never typed by hand where the contacts are known.
GENDER_WORD = {"pin": "вилка", "socket": "розетка"}
_GENDER_WORD_RE = re.compile(r"(?<!\w)(вилка|розетка)(?!\w)", flags=re.I)


def gender_title(title: str, contacts: str | None) -> str:
    """The nominative «вилка» / «розетка» in a title set to the word its contacts give; unknown contacts — unchanged."""
    word = GENDER_WORD.get(contacts or "")
    if not word:
        return title
    return _GENDER_WORD_RE.sub(lambda found: word.capitalize() if found[0][0].isupper() else word, title)


def _retitle_families() -> None:
    for family in FAMILIES:
        if family["id"] in MATING:
            family["title"] = gender_title(family["title"], MATING[family["id"]]["contacts"])


_retitle_families()

MATING_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("JST SM", "SMR-04V-B", "SMP-04V-BC"),
    ("JST RCY", "SYR-02T", "SYP-02T-1"),
    ("JST EL", "ELR-03V", "ELP-03V"),
    ("JST EL (4 конт. в 2 ряда)", "ELR-04NV", "ELP-04NV"),
    ("JST JWPF", "04T-JWPF-VSLE-S", "04R-JWPF-VSLE-S"),
    ("Molex PicoBlade", "51021-0400", "51047-0400"),
    ("Molex Micro-Fit 3.0 (2 ряда)", "43025-0400", "43020-0400"),
    ("Molex Micro-Fit 3.0 (1 ряд)", "43645-0400", "43640-0400"),
    ("Molex Nano-Fit", "105307-1204", "200277-1104"),
    ("Molex Nano-Fit (2 ряда)", "105308-1208", "201444-1108"),
    ("Molex Micro-Lock Plus 1,25", "2145260600", "2145252060"),
    ("Hirose DF1B", "DF1B-5ES-2.5RC", "DF1B-5EP-2.5RC"),
    ("Hirose DF1B (2 ряда)", "DF1B-10DES-2.5RC", "DF1B-10DEP-2.5RC"),
    ("Сервоконнектор JR", "JR-H-Female", "JR-H-Male"),
    ("D-Sub", "DS1033-09F", "DS1033-09M"),
    ("Deutsch DT", "DT04-4P", "DT06-4S"),
    ("TE Superseal 1.5", "282106-1", "282088-1"),
)
# Amass XT, Weipu SA8 and CNLINKO LP-12 carry their mating side (MATING_BY_ARTICLE) but are not pairs here: the round and
# XT bodies are drawn without the contact / shroud marks the pair checks need.


def mating_pairs() -> list[dict[str, Any]]:
    """Both halves of every pair in MATING_PAIRS, drawn by housing_of — the base of the «plug ≠ receptacle» checks."""
    return [{"series": series, "a": housing_of(first), "b": housing_of(second)} for series, first, second in MATING_PAIRS]


def normalize_article(value: object) -> str:
    article = str(value or "").strip().upper()
    article = re.sub(r"^(MOLEX|JST|HIROSE|JAE|TE(?:\s+CONNECTIVITY)?|CONNFLY|AMASS|AMP)\s+", "", article)
    return re.sub(r"\s+", "", article)


def housing_grid(rows: int, columns: int, count: int, order: str, start: str, snake: bool = False) -> list[list[int]]:
    grid = [[0 for _ in range(columns)] for _ in range(rows)]
    row_order = list(range(rows)) if start.startswith("T") else list(reversed(range(rows)))
    column_order = list(range(columns)) if start.endswith("L") else list(reversed(range(columns)))
    pin = 1
    axes = column_order if order == "col" else row_order
    for offset, axis in enumerate(axes):
        secondary = row_order if order == "col" else column_order
        if snake and offset % 2:
            secondary = list(reversed(secondary))
        for inner in secondary:
            row, column = (inner, axis) if order == "col" else (axis, inner)
            if pin <= count:
                grid[row][column] = pin
                pin += 1
    return grid


def housing_of(article: object) -> dict[str, Any] | None:
    normalized = normalize_article(article)
    if not normalized:
        return None
    alias = ALIASES.get(normalized)
    if alias:
        normalized = normalize_article(alias)
    for pattern, replacement in ALIAS_RULES:
        if re.search(pattern, normalized):
            normalized = normalize_article(re.sub(pattern, replacement, normalized, count=1))
    for family in FAMILIES:
        match = re.search(family["pattern"], normalized, flags=re.I)
        if not match:
            continue
        rows = int(family.get("rows", 1))
        male = None
        if family.get("kind") == "dsub":
            count = int(next((match[index] for index in family["count_groups"] if match[index]), "0"))
            gender = next((match[index] for index in family["gender_groups"] if match[index]), "F").upper()
            male = gender in {"M", "P"}
            if count not in {9, 15, 25, 37}:
                continue
            grid = dsub_grid(count, male)
            return _layout(article, family, alias, count, 2, len(grid[0]), grid, None, male)
        if family.get("kind") == "dt":
            count = int(match[int(family["count_group"])])
            if count not in DT_LAYOUTS:
                continue
            male = match[1] == "4"
            grid = [list(row) for row in DT_LAYOUTS[count]]
            if male:
                grid = [list(reversed(row)) for row in grid]
            return _layout(article, family, alias, count, len(grid), len(grid[0]), grid, None, male)
        if family.get("table"):
            count = int(family["table"].get(match[1], 0))
            columns = (count + rows - 1) // rows
        elif family.get("grid"):
            rows, columns = int(match[1]), int(match[2])
            count = rows * columns
        elif family.get("kind") in {"lug", "banana"}:
            rows, columns, count = 1, 1, 1
        elif family.get("fixed"):
            count = int(family["fixed"])
            columns = (count + rows - 1) // rows
        else:
            groups = family.get("count_groups", (family.get("count_group", 1),))
            raw_count = next((match[index] for index in groups if match[index]), "0")
            # `count_mul`: the part number gives contacts per row (Samtec ISD2-05-D — 2 × 5); `rows_by`: rows per count.
            count = int(raw_count) * int(family.get("count_mul", 1))
            rows = int(family.get("rows_by", {}).get(count, rows))
            columns = (count + rows - 1) // rows
        if count <= 0 or count > 200 or count > int(family.get("max", 200)):
            continue
        round_layout = None
        if family.get("round_auto"):
            if count < 2 or count > 12:
                continue
            round_layout = [
                {"pin": index + 1, "x": round(.55 * math.sin(2 * math.pi * index / count), 2),
                 "y": round(-.55 * math.cos(2 * math.pi * index / count), 2)}
                for index in range(count)
            ]
            rows = columns = 1
        elif family.get("kind") == "round":
            points = family["round"].get(count)
            if not points:
                continue
            male = match[int(family["male_group"])] == "P"
            round_layout = [
                {"pin": index, "x": -x if male else x, "y": y}
                for index, (x, y) in enumerate(points, start=1)
            ]
            rows = columns = 1
        if family.get("kind") == "round" and family.get("male_group"):
            male = match[int(family["male_group"])] == "P"
        grid = (
            [[1]] if round_layout or family.get("kind") in {"lug", "banana", "barrel"}
            else housing_grid(rows, columns, count, family.get("order", "row"), family.get("start", "TR"), bool(family.get("snake")))
        )
        color = family.get("color", IVORY)
        for pattern, replacement in family.get("color_by", ()):
            if re.search(pattern, normalized):
                color = replacement
        layout = _layout(article, family, alias, count, rows, columns, grid, round_layout, male, color)
        if family.get("body"):
            body = dict(family["body"])
            for pattern, extra in family.get("body_by", ()):
                if re.search(pattern, normalized):
                    body.update(extra)
            layout["body"] = body
        return layout
    return None


def _layout(
    article: object, family: Mapping[str, Any], alias: str | None, count: int, rows: int, columns: int,
    grid: list[list[int]], round_layout: list[dict[str, Any]] | None, male: bool | None, color: str | None = None,
) -> dict[str, Any]:
    return {
        "art": str(article or ""), "family": family["id"], "title": family["title"],
        "pitch": family.get("pitch"), "rows": rows, "cols": columns, "n": count,
        "kind": family.get("kind", "grid"), "color": color or family.get("color", IVORY),
        "latch": family.get("latch", "none"), "cavity": family.get("cavity", "square"),
        "labels": list(family["labels"]) if family.get("labels") else None,
        "grid": grid, "round": round_layout, "male": male, "erp_only": bool(family.get("erp_only")),
        "conf": "low" if alias else family.get("conf", "low"),
        "note": f"в наборе артикул без исполнения — нарисован {alias}" if alias else family.get("note", ""),
        "src": family.get("src", ""),
        # Gallery model template matched against the maker drawing (connectorModel.ts); "" — no checked model.
        "model": family.get("model", ""),
        "roof": roof_of(family, count, normalize_article(article)),
        # The maker's sheet shows a plain top for this housing (no lock, no window): its source, else "".
        "roof_plain": roof_plain_of(family, normalize_article(article)),
        "mate": mate_of(family, normalize_article(article), male),
    }


# Deutsch DT cavity layouts, wire side of the plug (DT06); pins run around the face. The receptacle (DT04) mirrors it.
DT_LAYOUTS: dict[int, tuple[tuple[int, ...], ...]] = {
    2: ((1, 2),), 3: ((1, 0, 2), (0, 3, 0)), 4: ((1, 2), (4, 3)), 6: ((1, 2, 3), (6, 5, 4)),
    8: ((1, 2, 3, 4), (8, 7, 6, 5)), 12: ((1, 2, 3, 4, 5, 6), (12, 11, 10, 9, 8, 7)),
}


def dsub_grid(count: int, male: bool) -> list[list[int]]:
    """D-Sub wire (solder-cup) side on a half-pitch grid: the wide row on top, the narrow row offset by half a pitch.
    On the mating face of the pin side 1 is top-left; the socket side and every wire side mirror that."""
    top = (count + 1) // 2
    columns = top * 2 - 1
    grid = [[0] * columns for _ in range(2)]
    for index in range(top):
        grid[0][index * 2] = index + 1
    for index in range(count - top):
        grid[1][index * 2 + 1] = top + index + 1
    # Wire side of the socket (F) reads 1→ from the left; of the pin side (M) — from the right.
    return [list(reversed(row)) for row in grid] if male else grid


# One representative article per family for the «Разъёмы» gallery (the most bought size where there is one).
CATALOG_SAMPLES: dict[str, str] = {
    "jst-gh": "GHR-04V-S", "jst-sh": "SHR-04V-S-B", "jst-nsh": "NSHR-04V-S", "jst-xh": "XHP-4", "jst-ph": "PHR-4",
    "jst-eh": "EHR-4", "jst-zh": "ZHR-4", "jst-vh": "VHR-4N", "jst-pa": "PAP-04V-S", "jst-ze": "ZER-04V-S",
    "jst-cz": "CZHR-04V-S", "jst-pni": "PNIRP-04V-S", "jst-rcy": "SYR-02T", "jst-rcy-p": "SYP-02T-1", "jst-sm-r": "SMR-04V-B",
    "jst-sm-p": "SMP-04V-BC", "molex-picoblade": "51021-0400", "molex-minifit-jr": "39012080",
    "molex-microfit": "43025-0400", "molex-microfit-1r": "43645-0400", "molex-sl-2r": "22-55-2081",
    "te-multilock-040": "174047-2", "connfly-ds1071": "I-DS1071-SCB02X5", "connfly-ds1069": "I-DS1069-SCW004",
    "connfly-ds1066": "I-DS1066-SCW02",
    "amass-xt": "XT60H-M", "amass-qs8": "QS8-S-M", "rj45": "RJ45 8P8C", "weipu-sa8": "SA812/S4",
    "cnlinko-lp12": "LP-12-C04PE", "usb-a": "USB-A", "jst-phd": "PHDR-14VS", "molex-microfit-plug": "43020-0400",
    "molex-microfit-plug-1r": "43640-0400", "molex-clikmate": "5025780600", "molex-picoclasp": "5011892010",
    "molex-picoclasp-1r": "501330-0600", "molex-milligrid": "51110-1051", "hirose-df13": "DF13-4S-1.25C",
    "hirose-df14": "DF14-4S-1.25C", "hirose-df11": "DF11-8DS-2C", "jae-fix": "FI-X30HL", "dsub": "DS1033-09F",
    "deutsch-dt": "DT06-4S", "te-superseal": "282106-1", "aviation-gx": "GX12M-4", "dc-barrel": "DC5.5x2.1",
    "lug": "НКИ 1.5-4", "banana": "BANANA 4",
    **CATALOG_FAMILY_SAMPLES,
}


def connector_catalog() -> list[dict[str, Any]]:
    """Every family drawn once from its sample article — the base the harness scheme and the ETK sketch use."""
    items = []
    for family in FAMILIES:
        sample = CATALOG_SAMPLES.get(family["id"])
        housing = housing_of(sample) if sample else None
        if housing:
            items.append({"family": family["id"], "title": family["title"], "sample": sample, "housing": housing})
    return items


def attach_housings(document: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    result = deepcopy(dict(document))
    for operation in result.get("operations") or []:
        if operation.get("type") not in {"insTerm", "prsTerm", "solderConn"}:
            continue
        housing = housing_of(operation.get("connector_article") or operation.get("connector"))
        if housing:
            operation["housing"] = housing
    topology = result.get("topology")
    if isinstance(topology, dict):
        for node in topology.get("nodes") or []:
            housing = housing_of(node.get("conn"))
            if housing:
                node["housing"] = housing
    return result
