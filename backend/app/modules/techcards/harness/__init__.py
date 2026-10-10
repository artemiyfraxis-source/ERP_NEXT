"""The ERP harness drawing, owned by techcards. Pure domain: model, length rules, TECHMAP-GRAPHER document
(de)serializer, derivations (`derive`, «Ручная сборка» in `assembly`), the stored document (`schema`), what may be
stored (`validate`), the `.techmap-project.zip` exchange (`exchange`). Use cases with the revision history:
`application` (over the unit of work); tables: `techcards.harness_infrastructure`; HTTP: `techcards.harness_api`.

TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission; source commit
e63befb85edb79f0c1635ac8314539ac24d2486a. See THIRD_PARTY_NOTICES.md.
"""

from .derive import ImpliedOp, SheetMatch, match_sheet, route_ops, wire_ops
from .model import HarnessDesign, SheetRow
from .rules import HarnessRuleError, blank_cut, cut_length, sheath_strip
from .snapshot import dump_design, parse_design

SOURCE_COMMIT = "e63befb85edb79f0c1635ac8314539ac24d2486a"

__all__ = [
    "HarnessDesign", "HarnessRuleError", "ImpliedOp", "SOURCE_COMMIT", "SheetMatch", "SheetRow", "blank_cut",
    "cut_length", "dump_design", "match_sheet", "parse_design", "route_ops", "sheath_strip", "wire_ops",
]
