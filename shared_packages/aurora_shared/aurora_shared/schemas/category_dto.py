"""
Aurora LTS — Category DTO canonical schemas (Sprint 4, Appendix L §3).

ONE source of truth for category-related Pydantic models. Both the HTTP
CRUD endpoints (app/routers/admin_exec.py) and the Copilot tool schemas
(app/services/copilot/tools.py) import from here. The Anthropic tool
input_schemas are GENERATED from these models via `model_json_schema()`
so the wire contract Claude sees is derived from the same class FastAPI
uses for request validation.

Adding a field here propagates to:
  • HTTP API validation (POST /categories, PATCH /categories/{id})
  • Anthropic tool schemas Claude sees in the Copilot
  • Executor validation when Approve & Build executes a tool_use
  • Future TypeScript codegen (if/when we add pydantic-to-ts)

DESIGN NOTES:
  • CategoryCreate is a discriminated union over `level`. A POST body
    can supply `{level: 1, name: ...}` for a sector OR `{level: 2,
    parent_id: 5, name: ...}` for a profession nested under sector 5.
  • CategoryProfessionCreate exposes BOTH `parent_id` (for the HTTP API,
    when the caller already knows the L1 row ID) and `parent_sector_name`
    (for Claude's blueprint, where the model references sectors by name
    so a newly-proposed sector can be referenced before it has an ID).
    The executor resolves `parent_sector_name` to an ID at apply time.
  • A backward-compat `model_validator` infers `level=1` for legacy
    POST bodies that omit the discriminator — preserves wire compat with
    pre-Sprint-4 clients.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


# ─────────────────────────────────────────────────────────────
# Shared base — common fields for both L1 sectors and L2 professions.
# ─────────────────────────────────────────────────────────────

class CategoryCore(BaseModel):
    """Shared fields across all category levels."""

    name: str = Field(min_length=1, max_length=120)
    name_he: Optional[str] = Field(default=None, max_length=120)
    name_ar: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = None
    icon_emoji: Optional[str] = Field(default=None, max_length=8)
    sort_order: int = 0
    is_active: bool = True


# ─────────────────────────────────────────────────────────────
# Level-1 (sector / branch) — top-level container.
# ─────────────────────────────────────────────────────────────

class CategorySectorCreate(CategoryCore):
    """Level-1 sector. Has no parent."""

    level: Literal[1] = 1
    parent_id: Optional[int] = None  # must be None; runtime-validated below

    @model_validator(mode="after")
    def _validate_sector(self):
        if self.parent_id is not None:
            raise ValueError(
                "Level-1 sector cannot have a parent_id. "
                "To create a profession under a sector, use level=2."
            )
        return self


# ─────────────────────────────────────────────────────────────
# Level-2 (profession / sub-category) — nested under a sector.
# ─────────────────────────────────────────────────────────────

class CategoryProfessionCreate(CategoryCore):
    """Level-2 profession nested under a sector.

    EXACTLY ONE of `parent_id` and `parent_sector_name` must be set:
      • parent_id            — HTTP API path (caller already knows the ID)
      • parent_sector_name   — Copilot blueprint path (Claude references
                                sectors by name so a NEW sector being
                                created in the same blueprint can be
                                referenced before having an ID)
    """

    level: Literal[2] = 2
    parent_id: Optional[int] = Field(default=None, ge=1)
    parent_sector_name: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _validate_profession_parent(self):
        if self.parent_id is None and not self.parent_sector_name:
            raise ValueError(
                "Profession requires either parent_id (existing sector id) "
                "OR parent_sector_name (sector name for blueprint resolution)."
            )
        if self.parent_id is not None and self.parent_sector_name:
            raise ValueError(
                "Profession may NOT set both parent_id and parent_sector_name."
            )
        return self


# ─────────────────────────────────────────────────────────────
# Discriminated union — used by POST /categories
# ─────────────────────────────────────────────────────────────
#
# FastAPI / Pydantic v2 dispatch on the `level` field at request time:
#   POST /categories {"level": 1, "name": "Construction"}  → CategorySectorCreate
#   POST /categories {"level": 2, "name": "Electricity", "parent_id": 3}
#       → CategoryProfessionCreate
#
# The discriminated union below preserves type-narrowing in callers
# (executor / Anthropic tool args) and surfaces clear 422 errors on
# misshapen bodies.

CategoryCreate = Annotated[
    Union[CategorySectorCreate, CategoryProfessionCreate],
    Field(discriminator="level"),
]


# ─────────────────────────────────────────────────────────────
# PATCH — partial update, all fields optional.
# ─────────────────────────────────────────────────────────────

class CategoryPatch(BaseModel):
    """Partial update — only set fields are applied."""

    name: Optional[str] = Field(default=None, max_length=120)
    name_he: Optional[str] = Field(default=None, max_length=120)
    name_ar: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = None
    icon_emoji: Optional[str] = Field(default=None, max_length=8)
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


# ─────────────────────────────────────────────────────────────
# Org assignment
# ─────────────────────────────────────────────────────────────

class CategoryAssignOrg(BaseModel):
    organization_id: int = Field(ge=1)


# ─────────────────────────────────────────────────────────────
# Delete — destructive, requires echoed confirmation (Copilot)
# ─────────────────────────────────────────────────────────────

class CategoryDelete(BaseModel):
    """Used by Copilot's `delete_category` tool. The CRUD HTTP endpoint
    DELETE /categories/{id} doesn't need confirm_understanding because
    the UI shows a modal."""

    category_id: int = Field(ge=1)
    confirm_understanding: str = Field(
        min_length=1,
        description="Founder must echo the exact category name to confirm.",
    )


# ─────────────────────────────────────────────────────────────
# Update — used by Copilot's `update_category` tool. The CRUD HTTP
# endpoint PATCH /categories/{id} uses CategoryPatch + the path id.
# ─────────────────────────────────────────────────────────────

class CategoryUpdate(BaseModel):
    category_id: int = Field(ge=1)
    name: Optional[str] = Field(default=None, max_length=120)
    name_he: Optional[str] = Field(default=None, max_length=120)
    name_ar: Optional[str] = Field(default=None, max_length=120)
    icon_emoji: Optional[str] = Field(default=None, max_length=8)
    is_active: Optional[bool] = None


# ─────────────────────────────────────────────────────────────
# Provisioning blueprint — Copilot's `propose_provisioning_blueprint`
# ─────────────────────────────────────────────────────────────

class ProvisioningBlueprint(BaseModel):
    """The structured plan Claude emits via tool_use; the founder
    approves via WebAuthn step-up, then the executor applies it."""

    summary: str = Field(
        min_length=1, max_length=400,
        description="One-line description for the approval card.",
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Why these categories serve the founder's request. "
                    "1-3 sentences.",
    )
    new_sectors: list[CategorySectorCreate] = Field(default_factory=list)
    new_professions: list[CategoryProfessionCreate] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# Search input (READ tool — Copilot's `search_existing_categories`)
# ─────────────────────────────────────────────────────────────

class CategorySearch(BaseModel):
    query: str = Field(min_length=1, max_length=120)
    level: Optional[int] = Field(default=None, ge=1, le=2)
