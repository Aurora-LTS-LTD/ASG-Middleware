"""
Aurora Copilot — Anthropic tool specifications (Sprint 4 refactor).

Tool input schemas are GENERATED from the canonical Pydantic DTOs in
`app/schemas/category_dto.py` via `model_json_schema()`. This eliminates
the dual-source-of-truth pain pre-Sprint-4 where admin_exec.py and
copilot/tools.py independently defined Pydantic models that drifted.

Adding a field to category_dto.py now propagates to:
  • Anthropic tool spec (Claude's view of allowed args)
  • Executor validation (the same Pydantic class)
  • HTTP API validation in admin_exec.py
  • Future TS codegen (pydantic-to-ts)
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.schemas.category_dto import (
    CategoryAssignOrg,
    CategoryDelete,
    CategorySearch,
    CategoryUpdate,
    ProvisioningBlueprint,
)


# ─────────────────────────────────────────────────────────────
# Re-export for executor.py backward compatibility.
# The executor imports these by their pre-refactor names.
# ─────────────────────────────────────────────────────────────

SearchCategoriesInput = CategorySearch
ProvisioningBlueprintInput = ProvisioningBlueprint
UpdateCategoryInput = CategoryUpdate
DeleteCategoryInput = CategoryDelete
AssignOrgInput = CategoryAssignOrg
# Pre-refactor names kept as aliases so executor.py doesn't churn:
from app.schemas.category_dto import (  # noqa: E402
    CategorySectorCreate as NewSectorInput,
    CategoryProfessionCreate as NewProfessionInput,
)


# ─────────────────────────────────────────────────────────────
# Anthropic tool specs — generated from Pydantic
# ─────────────────────────────────────────────────────────────

def _strip_pydantic_metadata(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Remove Pydantic-specific JSON-schema fields that Anthropic doesn't need.

    Pydantic generates schemas with `$defs` for sub-models and `title` /
    `default` markers. Anthropic accepts these but they're noise; this
    helper inlines `$defs` and removes top-level `title` for cleaner
    tool definitions.
    """
    if "$defs" not in schema:
        # Drop top-level title for cleaner display
        schema.pop("title", None)
        return schema

    defs = schema.pop("$defs", {})
    schema.pop("title", None)

    # Recursively inline $ref references. This is a defensive inliner —
    # Anthropic's tool engine does handle $refs but inlining keeps the
    # schema viewable to the founder when we render tool args in the UI.
    def _inline(obj: Any) -> Any:
        if isinstance(obj, dict):
            if "$ref" in obj and obj["$ref"].startswith("#/$defs/"):
                ref_name = obj["$ref"].split("/")[-1]
                inlined = defs.get(ref_name, {}).copy()
                # Preserve any sibling fields (e.g., descriptions overriding the def)
                merged = {**inlined, **{k: v for k, v in obj.items() if k != "$ref"}}
                return _inline(merged)
            return {k: _inline(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_inline(v) for v in obj]
        return obj

    return _inline(schema)


def _tool_spec(name: str, description: str, model_cls) -> Dict[str, Any]:
    """Build an Anthropic tool spec dict from a Pydantic model class."""
    schema = model_cls.model_json_schema()
    return {
        "name": name,
        "description": description,
        "input_schema": _strip_pydantic_metadata(schema),
    }


ANTHROPIC_TOOLS: List[Dict[str, Any]] = [
    _tool_spec(
        "search_existing_categories",
        (
            "Find existing sectors and professions matching a substring query. "
            "ALWAYS use this BEFORE proposing new categories to avoid duplicates. "
            "Returns a list with id, name, level, parent linkage."
        ),
        CategorySearch,
    ),
    _tool_spec(
        "propose_provisioning_blueprint",
        (
            "Propose a structured plan to create new sectors and/or professions "
            "in the Aurora taxonomy. NOTHING executes until the CEO approves "
            "via the UI (WebAuthn-gated). Always populate name_he for IL clients."
        ),
        ProvisioningBlueprint,
    ),
    _tool_spec(
        "update_category",
        (
            "Rename or re-icon an existing category. Requires category_id "
            "(obtain from search_existing_categories)."
        ),
        CategoryUpdate,
    ),
    _tool_spec(
        "delete_category",
        (
            "Permanently delete a category. DESTRUCTIVE — fails if any "
            "sub-categories or organizations are mapped. The "
            "`confirm_understanding` field MUST contain the exact category "
            "name as the founder echoed back."
        ),
        CategoryDelete,
    ),
    _tool_spec(
        "assign_org_to_category",
        (
            "Map an existing organization to a level-2 category (profession). "
            "Organizations CANNOT be mapped to level-1 sectors."
        ),
        CategoryAssignOrg,
    ),
]


# ─────────────────────────────────────────────────────────────
# Tool-name registry — executor maps name → handler
# ─────────────────────────────────────────────────────────────

TOOL_NAMES = {t["name"] for t in ANTHROPIC_TOOLS}

WRITE_TOOLS = {
    "propose_provisioning_blueprint",
    "update_category",
    "delete_category",
    "assign_org_to_category",
}
READ_TOOLS = TOOL_NAMES - WRITE_TOOLS  # {"search_existing_categories"}
