# app/api/endpoints/heartbeat.py
from fastapi import APIRouter, Depends, HTTPException, Header, Request, Request
from sqlalchemy.orm import Session
from sqlalchemy import desc, text
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import logging

from app.deps import get_db
from app.models.models import Agent, AgentActivity
from app.schemas import AgentHeartbeatRequest, AgentHeartbeatResponse

router = APIRouter()
logger = logging.getLogger(__name__)

# Проверка API ключа (опционально)
def verify_api_key(authorization: Optional[str] = Header(None)):
    """Проверяет API ключ из заголовка Authorization"""
    if not authorization:
        return None
    
    # Простая проверка для демо
    expected_key = "Bearer sk-agent-heartbeat-key-2024"
    if authorization != expected_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True

@router.post("/agents/heartbeat", response_model=AgentHeartbeatResponse)
def receive_agent_heartbeat(
    request: Request,
    heartbeat_data: Dict,
    db: Session = Depends(get_db),
    api_key_valid: Optional[bool] = Depends(verify_api_key)
):
    """
    Получает heartbeat от агента
    
    Heartbeat содержит:
    - timestamp: время отправки
    - agent_id: ID агента (USR0012345)
    - username: имя пользователя
    - role: роль агента
    - system_info: информация о системе
    - statistics: статистика работы
    - current_activity: текущая активность
    - status: статус агента (active/stopping)
    """
    
    logger.info(f"Received heartbeat from agent: {heartbeat_data.get('agent_id')}")
    
    # Извлекаем данные из heartbeat
    agent_id = heartbeat_data.get('agent_id')
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")
    
    # Проверяем, существует ли агент в БД
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    
    if not agent:
        # Создаем нового агента если его нет
        logger.info(f"Creating new agent: {agent_id}")
        agent = Agent(
            agent_id=agent_id,
            name=heartbeat_data.get('username', 'Unknown'),
            status="active",
            os_type=heartbeat_data.get('system_info', {}).get('platform', 'unknown'),
            config={
                'role': heartbeat_data.get('role'),
                'department': heartbeat_data.get('department'),
                'location': heartbeat_data.get('location')
            }
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)
    
    # Store client IP so frontend can auto-fill RDP target
    agent.injection_target = request.client.host

    agent.injection_target = request.client.host
    # Обновляем статус агента
    agent.status = heartbeat_data.get('status', 'active')
    agent.last_seen = datetime.utcnow()
    
    # Если есть информация о текущей активности, сохраняем её
    current_activity = heartbeat_data.get('current_activity')
    if current_activity:
        agent.last_activity = current_activity.get('application', 'Unknown')
    
    db.commit()
    
    # Сохраняем heartbeat как активность
    activity = AgentActivity(
        agent_id=agent.id,
        activity_type="heartbeat",
        activity_data=heartbeat_data,
        timestamp=datetime.utcnow()
    )
    db.add(activity)
    
    # Если есть статистика, сохраняем её отдельно
    statistics = heartbeat_data.get('statistics')
    if statistics:
        stats_activity = AgentActivity(
            agent_id=agent.id,
            activity_type="statistics",
            activity_data=statistics,
            timestamp=datetime.utcnow()
        )
        db.add(stats_activity)
    
    db.commit()
    
    # Формируем ответ
    response = {
        "status": "received",
        "agent_id": agent_id,
        "timestamp": datetime.utcnow().isoformat(),
        "message": "Heartbeat processed successfully",
        "next_heartbeat_in": 86400  # 24 часа в секундах
    }
    
    # Можем отправить команды агенту в ответе
    if agent.status == "active":
        response["commands"] = []  # Здесь можно добавить команды для агента
    
    return response

@router.get("/agents/{agent_id}/heartbeats")
def get_agent_heartbeats(
    agent_id: str,
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """Получает историю heartbeat'ов агента"""
    
    agent = db.query(Agent).filter(Agent.agent_id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Получаем heartbeat активности
    heartbeats = db.query(AgentActivity).filter(
        AgentActivity.agent_id == agent.id,
        AgentActivity.activity_type == "heartbeat"
    ).order_by(desc(AgentActivity.timestamp)).limit(limit).all()
    
    return {
        "agent_id": agent_id,
        "agent_name": agent.name,
        "last_seen": agent.last_seen.isoformat() if agent.last_seen else None,
        "status": agent.status,
        "heartbeats": [
            {
                "timestamp": hb.timestamp.isoformat(),
                "data": hb.activity_data
            } for hb in heartbeats
        ]
    }

@router.get("/agents/active")
def get_active_agents(
    threshold_minutes: int = 30,
    db: Session = Depends(get_db)
):
    """Получает список активных агентов (те, кто отправлял heartbeat недавно)"""
    
    threshold_time = datetime.utcnow() - timedelta(minutes=threshold_minutes)
    
    active_agents = db.query(Agent).filter(
        Agent.last_seen >= threshold_time,
        Agent.status == "active"
    ).all()
    
    return {
        "threshold_minutes": threshold_minutes,
        "active_count": len(active_agents),
        "agents": [
            {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "last_seen": agent.last_seen.isoformat() if agent.last_seen else None,
                "last_activity": agent.last_activity,
                "ip": agent.injection_target,
                "role": db.execute(text("SELECT agent_role FROM agents WHERE id = :id"), {"id": agent.id}).scalar() or (agent.config.get("role") if agent.config else "user")
            } for agent in active_agents
        ]
    }

@router.get("/agents/statistics/summary")
def get_agents_statistics_summary(db: Session = Depends(get_db)):
    """Получает сводную статистику по всем агентам"""
    
    # Общее количество агентов
    total_agents = db.query(Agent).count()
    
    # Активные агенты (heartbeat за последние 30 минут)
    threshold_time = datetime.utcnow() - timedelta(minutes=30)
    active_agents = db.query(Agent).filter(
        Agent.last_seen >= threshold_time,
        Agent.status == "active"
    ).count()
    
    # Неактивные агенты
    inactive_agents = db.query(Agent).filter(
        Agent.status != "active"
    ).count()
    
    # Последние heartbeat'ы
    recent_heartbeats = db.query(AgentActivity).filter(
        AgentActivity.activity_type == "heartbeat"
    ).order_by(desc(AgentActivity.timestamp)).limit(5).all()
    
    return {
        "summary": {
            "total_agents": total_agents,
            "active_agents": active_agents,
            "inactive_agents": inactive_agents,
            "percentage_active": (active_agents / total_agents * 100) if total_agents > 0 else 0
        },
        "recent_heartbeats": [
            {
                "agent_id": hb.agent.agent_id if hb.agent else "unknown",
                "timestamp": hb.timestamp.isoformat(),
                "status": hb.activity_data.get('status', 'unknown')
            } for hb in recent_heartbeats if hb.agent
        ]
    }
