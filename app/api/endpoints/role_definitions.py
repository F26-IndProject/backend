# NEW FILE: ~/lisa/backend/app/api/endpoints/role_definitions.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from typing import Dict, Any
from app.deps import get_db
router = APIRouter()
# ─── SCHEMAS ─────────────────────────────────────────────────────────────────
class RoleDefinitionCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    os_type: str  # 'windows' or 'linux'
    actions: Dict[str, Any]  # full action config JSON
class RoleDefinitionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    actions: Optional[Dict[str, Any]] = None
class RoleDefinitionResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    os_type: str
    actions: Dict[str, Any]
    is_builtin: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
# ─── ENDPOINTS ───────────────────────────────────────────────────────────────
@router.post("/role-definitions", response_model=RoleDefinitionResponse)
def create_role_definition(role: RoleDefinitionCreate, db: Session = Depends(get_db)):
    """Create a new custom role definition. If a soft-deleted role with the same name exists, reactivate it."""
    if role.os_type not in ("windows", "linux"):
        raise HTTPException(status_code=400, detail="os_type must be 'windows' or 'linux'")
    import json
    # Check if active role with same name exists
    existing_active = db.execute(
        text("SELECT id FROM agent_role_definitions WHERE name = :name AND is_active = TRUE"),
        {"name": role.name}
    ).fetchone()
    if existing_active:
        raise HTTPException(status_code=400, detail=f"Role '{role.name}' already exists")
    # Check if soft-deleted role with same name exists — reactivate it
    existing_inactive = db.execute(
        text("SELECT id FROM agent_role_definitions WHERE name = :name AND is_active = FALSE"),
        {"name": role.name}
    ).fetchone()
    if existing_inactive:
        result = db.execute(
            text("""
                UPDATE agent_role_definitions
                SET description = :description, actions = :actions, is_active = TRUE, updated_at = NOW()
                WHERE id = :id
                RETURNING id, name, description, os_type, actions, is_builtin, is_active, created_at, updated_at
            """),
            {
                "id": existing_inactive[0],
                "description": role.description or "",
                "actions": json.dumps(role.actions)
            }
        )
        db.commit()
        row = result.fetchone()
        return _row_to_dict(row)
    # Insert new record
    result = db.execute(
        text("""
            INSERT INTO agent_role_definitions (name, description, os_type, actions, is_builtin, is_active)
            VALUES (:name, :description, :os_type, :actions, FALSE, TRUE)
            RETURNING id, name, description, os_type, actions, is_builtin, is_active, created_at, updated_at
        """),
        {
            "name": role.name,
            "description": role.description or "",
            "os_type": role.os_type,
            "actions": json.dumps(role.actions)
        }
    )
    db.commit()
    row = result.fetchone()
    return _row_to_dict(row)
@router.get("/role-definitions", response_model=List[RoleDefinitionResponse])
def list_role_definitions(
    os_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """List all role definitions — builtin shown but locked. Filter by os_type."""
    query = "SELECT id, name, description, os_type, actions, is_builtin, is_active, created_at, updated_at FROM agent_role_definitions WHERE is_active = TRUE"
    params = {}
    if os_type:
        query += " AND os_type = :os_type"
        params["os_type"] = os_type
    query += " ORDER BY is_builtin DESC, name ASC"
    results = db.execute(text(query), params).fetchall()
    return [_row_to_dict(r) for r in results]
@router.get("/role-definitions/{role_id}", response_model=RoleDefinitionResponse)
def get_role_definition(role_id: int, db: Session = Depends(get_db)):
    """Get a single role definition by ID."""
    result = db.execute(
        text("SELECT id, name, description, os_type, actions, is_builtin, is_active, created_at, updated_at FROM agent_role_definitions WHERE id = :id"),
        {"id": role_id}
    ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Role definition not found")
    return _row_to_dict(result)
@router.get("/role-definitions/by-name/{name}", response_model=RoleDefinitionResponse)
def get_role_definition_by_name(name: str, db: Session = Depends(get_db)):
    """Get a role definition by name — used by agent heartbeat polling."""
    result = db.execute(
        text("SELECT id, name, description, os_type, actions, is_builtin, is_active, created_at, updated_at FROM agent_role_definitions WHERE name = :name AND is_active = TRUE"),
        {"name": name}
    ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Role definition not found")
    return _row_to_dict(result)
@router.put("/role-definitions/{role_id}", response_model=RoleDefinitionResponse)
def update_role_definition(role_id: int, role: RoleDefinitionUpdate, db: Session = Depends(get_db)):
    """Update a custom role. Builtin roles cannot be edited."""
    existing = db.execute(
        text("SELECT id, is_builtin FROM agent_role_definitions WHERE id = :id"),
        {"id": role_id}
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Role definition not found")
    if existing[1]:  # is_builtin
        raise HTTPException(status_code=403, detail="Built-in roles cannot be edited")
    import json
    updates = []
    params = {"id": role_id}
    if role.name is not None:
        updates.append("name = :name")
        params["name"] = role.name
    if role.description is not None:
        updates.append("description = :description")
        params["description"] = role.description
    if role.actions is not None:
        updates.append("actions = :actions")
        params["actions"] = json.dumps(role.actions)
    updates.append("updated_at = NOW()")
    db.execute(
        text(f"UPDATE agent_role_definitions SET {', '.join(updates)} WHERE id = :id"),
        params
    )
    db.commit()
    result = db.execute(
        text("SELECT id, name, description, os_type, actions, is_builtin, is_active, created_at, updated_at FROM agent_role_definitions WHERE id = :id"),
        {"id": role_id}
    ).fetchone()
    return _row_to_dict(result)
@router.delete("/role-definitions/{role_id}")
def delete_role_definition(role_id: int, db: Session = Depends(get_db)):
    """Soft delete a custom role. Builtin roles cannot be deleted."""
    existing = db.execute(
        text("SELECT id, is_builtin, name FROM agent_role_definitions WHERE id = :id"),
        {"id": role_id}
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Role definition not found")
    if existing[1]:  # is_builtin
        raise HTTPException(status_code=403, detail="Built-in roles cannot be deleted")
    # Block delete if any agent is currently assigned this role
    agents_using = db.execute(
        text("SELECT COUNT(*) AS cnt FROM agents WHERE agent_role = :name"),
        {"name": existing[2]}
    ).fetchone()
    if agents_using and agents_using[0] > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete — role '{existing[2]}' is assigned to {agents_using[0]} agent(s). Reassign them first."
        )
    db.execute(
        text("UPDATE agent_role_definitions SET is_active = FALSE, updated_at = NOW() WHERE id = :id"),
        {"id": role_id}
    )
    db.commit()
    return {"message": f"Role '{existing[2]}' deleted successfully"}
# ─── HELPER ──────────────────────────────────────────────────────────────────
def _row_to_dict(row):
    import json
    actions = row[4]
    if isinstance(actions, str):
        actions = json.loads(actions)
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "os_type": row[3],
        "actions": actions,
        "is_builtin": row[5],
        "is_active": row[6],
        "created_at": row[7],
        "updated_at": row[8],
    }
