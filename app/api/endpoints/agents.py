from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, text
from typing import List, Optional
from pydantic import BaseModel
from typing import Dict, Any
import uuid
import os
import json
import tempfile
from datetime import datetime

from app.deps import get_db
from app.models.models import Role, BehaviorTemplate, Agent, AgentActivity
from app.schemas import AgentConfig, AgentResponse, AgentGenerateResponse, DeploymentRequest

SHARED_CONFIG_DIR = "/tmp/shared_configs"

router = APIRouter()


@router.post("/agents/generate", response_model=AgentGenerateResponse)
def generate_agent(config: AgentConfig, db: Session = Depends(get_db)):
    """Generate agent configuration using database models"""
    role = db.query(Role).filter(Role.id == config.role_id, Role.is_active == True).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    
    template = db.query(BehaviorTemplate).filter(
        BehaviorTemplate.id == config.template_id,
        BehaviorTemplate.is_active == True
    ).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    if template.os_type != config.os_type:
        raise HTTPException(
            status_code=400,
            detail=f"Template OS ({template.os_type}) doesn't match requested OS ({config.os_type})"
        )
    
    agent_id = f"USR{str(uuid.uuid4().int)[:7]}"

    db_agent = Agent(
        agent_id=agent_id,
        name=config.name,
        role_id=config.role_id,
        template_id=config.template_id,
        os_type=config.os_type,
        injection_target=config.injection_target,
        config=config.custom_config,
        status="configured"
    )

    db.add(db_agent)
    db.commit()
    db.refresh(db_agent)

    agent_config = {
        "agent_id": agent_id,
        "name": config.name,
        "os": config.os_type,
        "template_id": config.template_id,
        "role": {
            "name": role.name,
            "description": role.description,
            "category": role.category
        },
        "behavior_template": template.template_data,
        "injection_target": config.injection_target,
        "custom_config": config.custom_config or {},
        "generated_at": datetime.utcnow().isoformat(),
        "version": "1.0",
    }
    try:
        task_filename = f"build-{agent_id}.json"
        task_filepath = os.path.join("/tmp", task_filename)
        with open(task_filepath, 'w') as f:
            json.dump(agent_config, f, indent=4)
    except Exception as e:
        print(f"WARNING: Failed to create deployment task file: {e}")

    return AgentGenerateResponse(
        agent_id=agent_id,
        message=f"Agent '{config.name}' configured successfully",
        config=agent_config,
        download_url=f"/api/agents/{agent_id}/config/download",
        status_url=f"/api/agents/{agent_id}/status"
    )

@router.post("/agents/{agent_id}/deploy", status_code=202)
def trigger_deployment(
    agent_id: str,
    deployment_info: DeploymentRequest,
    db: Session = Depends(get_db)
):
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    deployment_task = {
        "agent_id": agent.agent_id,
        "server_ip": deployment_info.server_ip,
        "server_user": deployment_info.server_user,
        "server_password": deployment_info.server_password,
        "template_id": agent.template_id,
        "os_type": agent.os_type,
        "agent_build_config": {
            "name": agent.name,
            "os_type": agent.os_type,
            "custom_config": agent.config
        }
    }

    if not os.path.exists(SHARED_CONFIG_DIR):
        os.makedirs(SHARED_CONFIG_DIR)

    task_filename = f"deploy_task_{uuid.uuid4()}.json"
    task_filepath = os.path.join(SHARED_CONFIG_DIR, task_filename)

    try:
        with open(task_filepath, 'w') as f:
            json.dump(deployment_task, f, indent=4)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create deployment task file: {e}")
        
    agent.status = "deploying"
    agent.injection_target = deployment_info.server_ip
    db.commit()

    return {
        "status": "deployment_task_created",
        "agent_id": agent_id,
        "task_file": task_filename,
        "message": "Deployment task has been submitted."
    }

@router.get("/agents/{agent_id}/config/download")
def download_agent_config(agent_id: str, db: Session = Depends(get_db)):
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    config = {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "target_os": agent.os_type,
        "role": {
            "name": agent.role.name if agent.role else "Unknown",
            "description": agent.role.description if agent.role else "",
            "category": agent.role.category if agent.role else ""
        },
        "behavior_template": agent.template.template_data if agent.template else {},
        "injection_target": agent.injection_target,
        "custom_config": agent.config or {},
        "server_url": "http://localhost:8000",
        "heartbeat_interval": 86400,
        "created_at": agent.created_at.isoformat(),
        "version": "1.0"
    }
    
    content = json.dumps(config, indent=2)
    filename = f"{agent_id}_config.json"
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    
    return FileResponse(
        path=tmp_path,
        filename=filename,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.get("/agents", response_model=List[AgentResponse])
def list_agents(
    status: Optional[str] = None,
    os_type: Optional[str] = None,
    role_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    query = db.query(Agent)
    if status:
        query = query.filter(Agent.status == status)
    if os_type:
        query = query.filter(Agent.os_type == os_type)
    if role_id:
        query = query.filter(Agent.role_id == role_id)
    agents = query.order_by(desc(Agent.created_at)).offset(skip).limit(limit).all()
    return agents

@router.get("/agents/{agent_id}/status")
def get_agent_status(agent_id: str, db: Session = Depends(get_db)):
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    recent_activities = db.query(AgentActivity).filter(
        AgentActivity.agent_id == agent.id
    ).order_by(desc(AgentActivity.timestamp)).limit(10).all()
    
    return {
        "agent": {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "status": agent.status,
            "os_type": agent.os_type,
            "role": agent.role.name if agent.role else "Unknown",
            "template": agent.template.name if agent.template else "Unknown",
            "injection_target": agent.injection_target,
            "last_seen": agent.last_seen,
            "created_at": agent.created_at
        },
        "recent_activities": [
            {
                "id": activity.id,
                "type": activity.activity_type,
                "data": activity.activity_data,
                "timestamp": activity.timestamp
            } for activity in recent_activities
        ]
    }


class AgentRoleUpdate(BaseModel):
    agent_role: str

@router.patch("/agents/{agent_id}/role")
def update_agent_role(agent_id: str, role_update: AgentRoleUpdate, db: Session = Depends(get_db)):
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    db.execute(
        text("UPDATE agents SET agent_role = :role WHERE agent_id = :agent_id"),
        {"role": role_update.agent_role, "agent_id": agent_id}
    )
    db.commit()

    return {
        "status": "updated",
        "agent_id": agent_id,
        "agent_role": role_update.agent_role,
        "message": "Agent will switch role within 5 minutes"
    }


class AgentScheduleUpdate(BaseModel):
    schedule_id: Optional[int] = None

@router.patch("/agents/{agent_id}/schedule")
def update_agent_schedule(agent_id: str, body: AgentScheduleUpdate, db: Session = Depends(get_db)):
    """Assign or remove a work schedule for a specific agent."""
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if body.schedule_id is not None:
        exists = db.execute(
            text("SELECT id FROM agent_schedules WHERE id = :id"),
            {"id": body.schedule_id}
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Schedule not found")

    db.execute(
        text("UPDATE agents SET agent_schedule_id = :sid WHERE agent_id = :aid"),
        {"sid": body.schedule_id, "aid": agent_id}
    )
    db.commit()

    return {
        "status": "updated",
        "agent_id": agent_id,
        "schedule_id": body.schedule_id,
        "message": "Agent will use new schedule within 5 minutes"
    }


class AgentBreakUpdate(BaseModel):
    break_id: Optional[int] = None

@router.patch("/agents/{agent_id}/break")
def update_agent_break(agent_id: str, body: AgentBreakUpdate, db: Session = Depends(get_db)):
    """Assign or remove a break time for a specific agent."""
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if body.break_id is not None:
        exists = db.execute(
            text("SELECT id FROM break_times WHERE id = :id"),
            {"id": body.break_id}
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Break time not found")

    db.execute(
        text("UPDATE agents SET agent_break_id = :bid WHERE agent_id = :aid"),
        {"bid": body.break_id, "aid": agent_id}
    )
    db.commit()

    return {
        "status": "updated",
        "agent_id": agent_id,
        "break_id": body.break_id,
        "message": "Agent will use new break time within 5 minutes"
    }


@router.get("/agents/{agent_id}/activities")
def get_agent_activities(
    agent_id: str,
    limit: int = 20,
    db: Session = Depends(get_db)
):
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    activities = db.query(AgentActivity).filter(
        AgentActivity.agent_id == agent.id
    ).order_by(desc(AgentActivity.timestamp)).limit(limit).all()

    result = db.execute(
        text("SELECT agent_role FROM agents WHERE agent_id = :agent_id"),
        {"agent_id": agent_id}
    ).fetchone()
    agent_role = result[0] if result and result[0] else "user"

    # Get assigned schedule if any
    sched = db.execute(
        text("""
            SELECT s.id, s.name, s.work_start, s.work_end, s.work_days
            FROM agents a
            LEFT JOIN agent_schedules s ON s.id = a.agent_schedule_id
            WHERE a.agent_id = :agent_id
        """),
        {"agent_id": agent_id}
    ).fetchone()

    schedule_info = None
    if sched and sched[0]:
        days = sched[4]
        schedule_info = {
            "id":         sched[0],
            "name":       sched[1],
            "work_start": sched[2],
            "work_end":   sched[3],
            "work_days":  json.loads(days) if isinstance(days, str) else days
        }

    # Get assigned break time if any
    brk = db.execute(
        text("""
            SELECT b.id, b.name, b.break_start, b.break_end
            FROM agents a
            LEFT JOIN break_times b ON b.id = a.agent_break_id
            WHERE a.agent_id = :agent_id
        """),
        {"agent_id": agent_id}
    ).fetchone()

    break_info = None
    if brk and brk[0]:
        break_info = {
            "id":          brk[0],
            "name":        brk[1],
            "break_start": brk[2],
            "break_end":   brk[3]
        }

    return {
        "agent_id":      agent.agent_id,
        "name":          agent.name,
        "status":        agent.status,
        "os_type":       agent.os_type,
        "agent_role":    agent_role,
        "schedule":      schedule_info,
        "break_time":    break_info,
        "last_seen":     agent.last_seen,
        "last_activity": agent.last_activity,
        "created_at":    agent.created_at,
        "activities": [
            {
                "activity_type": a.activity_type,
                "timestamp":     a.timestamp.isoformat() if a.timestamp else None,
                "activity_data": a.activity_data
            }
            for a in activities
        ]
    }


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str, db: Session = Depends(get_db)):
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    db.query(AgentActivity).filter(AgentActivity.agent_id == agent.id).delete()
    db.delete(agent)
    db.commit()

    return {"message": f"Agent '{agent.name}' deleted", "agent_id": agent_id}
