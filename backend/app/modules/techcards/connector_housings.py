"""Connector-family pin layouts ported from current SUPPLYMAP TcHousing.gs."""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Mapping

from .connector_catalog_data import CATALOG_FAMILIES, CATALOG_FAMILY_SAMPLES


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
         body={"extra": 4.8, "h": 4.1, "len": 7.75, "lock_inset": 1.8, "lock_h": 1.6, "win": (1.2, 1.6)}),
    _jst("jst-ph", "PH", r"^PHR-(\d+)", 2.0, "windows", conf="high", src="JST ePH, Housing", model="slots",
         note="по чертежу JST ePH: окна фиксаторов снизу, контакт 1 слева",
         body={"extra": 3.8, "h": 4.5, "len": 6.85, "win": (1.0, 1.6)}),
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
    _jst("jst-sm-r", "SM (розетка)", r"^SMR-0*(\d+)V", 2.5, "lever", max=12, start="TL", conf="high", color_by=((r"V-B", BLACK),)),
    _jst("jst-sm-p", "SM (вилка)", r"^SMP-0*(\d+)V", 2.5, "lever", max=12, start="TR", conf="high", color_by=((r"V-B", BLACK),)),
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
     "body": {"extra": 5.4, "h": 11.6, "len": 19.6, "lock_w": 5.4, "lock_h": 2.8, "win": (3.4, 3.4)}},
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
    {"id": "molex-sl-2r", "title": "Molex SL (2 ряда)", "pattern": r"^22-?55-?2(\d{2})1$", "pitch": 2.54, "rows": 2, "order": "col", "start": "TR", "latch": "friction", "color": BLACK, "conf": "low"},
    {"id": "te-multilock-040", "title": "TE Multilock 040", "pattern": r"^1740(?:47|45|46|44)-\d$", "fixed": 20, "pitch": 2.5, "rows": 2, "order": "col", "start": "TR", "latch": "lever", "color": BLACK, "conf": "low"},
    # DS1071 — Connfly C-DS1071-XXXXX-B (connfly.com/static/upload/file/pdf/DS1071.pdf): the female housing is drawn
    # symmetric with no circuit-1 mark (Dupont has no maker drawing of its own), so the numbering stays unchecked.
    {"id": "connfly-ds1071", "title": "Connfly DS1071 (Dupont 2,54)", "pattern": r"^I-DS1071-SC[BW]0?(\d)X0?(\d{1,2})", "grid": True, "pitch": 2.54, "order": "col", "start": "TR", "latch": "none", "color": BLACK, "conf": "low",
     "note": "на чертеже Connfly DS1071 корпус без метки контакта 1 — контакт 1 по ответной вилке"},
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
