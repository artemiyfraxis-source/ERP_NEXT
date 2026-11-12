"""Procurement article keys exposed through the workflow boundary.

code_key remains the historical storage key (nomenclature, UPD and price history).
comparison_code_key is an explicit opt-in for matching; never persist its output.
"""

from app.modules.procurement.document_match import code_key, comparison_code_key

__all__ = ["code_key", "comparison_code_key"]
