"""RBAC-ready permission dependency for the CEO Command Center admin APIs.

Every admin endpoint declares its `(module, action)` via
`Depends(require_permission("customers", "suspend"))`. This LAYERS ON TOP of
`require_admin` (which still enforces IAP + admin role + native session), so:

  v3.0 behaviour: an authenticated admin has EVERY permission (admin ⇒ all).
  Later: consult the roles/permissions/role_permissions tables to grant
  Support / Finance / Sales / Operations / Viewer scoped access — WITHOUT
  changing any endpoint (the (module, action) declarations are already there).

Keeping the declaration at every endpoint now is the whole point: enabling
granular RBAC later becomes data + a lookup here, not an endpoint refactor.
"""
from __future__ import annotations

from fastapi import Depends

from aurora_shared.database.models import User
from aurora_shared.middleware.auth_middleware import require_admin


def require_permission(module: str, action: str):
    """Return a FastAPI dependency enforcing `(module, action)`.

    v3.0: `require_admin` gate + admin-has-all. The `module`/`action` are
    recorded on the closure for future granular enforcement.
    """
    def _dep(current_user: User = Depends(require_admin)) -> User:
        # Future granular path (pseudocode, intentionally not active in v3.0):
        #   if current_user.role == "admin": return current_user
        #   if _role_has_permission(current_user.role, module, action, db):
        #       return current_user
        #   raise HTTPException(403, {"error": "forbidden",
        #       "message": f"Missing permission {module}:{action}"})
        return current_user

    _dep.__name__ = f"require_permission_{module}_{action}"
    return _dep
