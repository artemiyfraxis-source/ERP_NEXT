"""Port: the part-number key of procurement (`document_match.code_key`) for modules that match articles too
(purchasing nomenclature and requests for quotes) — a module reaches another one only through app/workflows."""

from app.modules.procurement.document_match import code_key

__all__ = ["code_key"]
