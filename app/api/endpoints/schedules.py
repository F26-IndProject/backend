# app/api/endpoints/schedules.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime, date

from app.deps import get_db

router = APIRouter()


class ScheduleCreate(BaseModel):
    name:       str
    work_days:  List[int]
    work_start: str
    work_end:   str

class ScheduleUpdate(BaseModel):
    name:       Optional[str]       = None
    work_days:  Optional[List[int]] = None
    work_start: Optional[str]       = None
    work_end:   Optional[str]       = None

class HolidayCreate(BaseModel):
    date: date
    name: Optional[str] = None

class BreakTimeCreate(BaseModel):
    name:        str
    break_start: str
    break_end:   str

class BreakTimeUpdate(BaseModel):
    name:        Optional[str] = None
    break_start: Optional[str] = None
    break_end:   Optional[str] = None


# ── Schedules ─────────────────────────────────────────────────────────────────

@router.get("/schedules")
def list_schedules(db: Session = Depends(get_db)):
    rows = db.execute(text(
        "SELECT id, name, work_days, work_start, work_end, is_default, created_at, updated_at "
        "FROM agent_schedules ORDER BY created_at DESC"
    )).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/schedules", status_code=201)
def create_schedule(body: ScheduleCreate, db: Session = Depends(get_db)):
    import json
    row = db.execute(text(
        "INSERT INTO agent_schedules (name, work_days, work_start, work_end) "
        "VALUES (:name, :days, :start, :end) RETURNING *"
    ), {"name": body.name, "days": json.dumps(body.work_days),
        "start": body.work_start, "end": body.work_end}).fetchone()
    db.commit()
    return dict(row._mapping)


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: int, body: ScheduleUpdate, db: Session = Depends(get_db)):
    import json
    if not db.execute(text("SELECT id FROM agent_schedules WHERE id = :id"),
                      {"id": schedule_id}).fetchone():
        raise HTTPException(status_code=404, detail="Schedule not found")
    updates = {}
    if body.name       is not None: updates["name"]       = body.name
    if body.work_days  is not None: updates["work_days"]  = json.dumps(body.work_days)
    if body.work_start is not None: updates["work_start"] = body.work_start
    if body.work_end   is not None: updates["work_end"]   = body.work_end
    updates["updated_at"] = datetime.utcnow()
    updates["id"] = schedule_id
    db.execute(text(
        f"UPDATE agent_schedules SET {', '.join(f'{k}=:{k}' for k in updates if k != 'id')} WHERE id = :id"
    ), updates)
    db.commit()
    return {"status": "updated", "id": schedule_id}


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.execute(text(
        "SELECT is_default FROM agent_schedules WHERE id = :id"
    ), {"id": schedule_id}).fetchone()

    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if schedule[0]:
        raise HTTPException(status_code=400, detail="Cannot delete a default schedule")

    agents_using = db.execute(text(
        "SELECT name FROM agents WHERE agent_schedule_id = :id"
    ), {"id": schedule_id}).fetchall()

    if agents_using:
        names = [row[0] for row in agents_using]
        raise HTTPException(
            status_code=400,
            detail=f"Schedule is assigned to: {', '.join(names)}"
        )

    result = db.execute(text("DELETE FROM agent_schedules WHERE id = :id"), {"id": schedule_id})
    db.commit()
    return {"status": "deleted", "id": schedule_id}


# ── Holidays ──────────────────────────────────────────────────────────────────

@router.get("/holidays")
def list_holidays(db: Session = Depends(get_db)):
    rows = db.execute(text(
        "SELECT id, date, name, created_at FROM public_holidays ORDER BY date ASC"
    )).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/holidays", status_code=201)
def add_holiday(body: HolidayCreate, db: Session = Depends(get_db)):
    try:
        row = db.execute(text(
            "INSERT INTO public_holidays (date, name) VALUES (:date, :name) RETURNING *"
        ), {"date": body.date, "name": body.name}).fetchone()
        db.commit()
        return dict(row._mapping)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="A holiday on this date already exists")


@router.delete("/holidays/{holiday_id}")
def delete_holiday(holiday_id: int, db: Session = Depends(get_db)):
    result = db.execute(text("DELETE FROM public_holidays WHERE id = :id"), {"id": holiday_id})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Holiday not found")
    return {"status": "deleted", "id": holiday_id}


# ── Break Times ───────────────────────────────────────────────────────────────

@router.get("/break-times")
def list_break_times(db: Session = Depends(get_db)):
    rows = db.execute(text(
        "SELECT id, name, break_start, break_end, is_default, created_at "
        "FROM break_times ORDER BY created_at ASC"
    )).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/break-times", status_code=201)
def create_break_time(body: BreakTimeCreate, db: Session = Depends(get_db)):
    row = db.execute(text(
        "INSERT INTO break_times (name, break_start, break_end) "
        "VALUES (:name, :start, :end) RETURNING *"
    ), {"name": body.name, "start": body.break_start, "end": body.break_end}).fetchone()
    db.commit()
    return dict(row._mapping)


@router.put("/break-times/{break_id}")
def update_break_time(break_id: int, body: BreakTimeUpdate, db: Session = Depends(get_db)):
    if not db.execute(text("SELECT id FROM break_times WHERE id = :id"), {"id": break_id}).fetchone():
        raise HTTPException(status_code=404, detail="Break time not found")
    updates = {}
    if body.name        is not None: updates["name"]        = body.name
    if body.break_start is not None: updates["break_start"] = body.break_start
    if body.break_end   is not None: updates["break_end"]   = body.break_end
    if not updates:
        return {"status": "no changes"}
    updates["id"] = break_id
    db.execute(text(
        f"UPDATE break_times SET {', '.join(f'{k}=:{k}' for k in updates if k != 'id')} WHERE id = :id"
    ), updates)
    db.commit()
    return {"status": "updated", "id": break_id}


@router.delete("/break-times/{break_id}")
def delete_break_time(break_id: int, db: Session = Depends(get_db)):
    brk = db.execute(text(
        "SELECT is_default FROM break_times WHERE id = :id"
    ), {"id": break_id}).fetchone()

    if not brk:
        raise HTTPException(status_code=404, detail="Break time not found")

    if brk[0]:
        raise HTTPException(status_code=400, detail="Cannot delete a default break time")

    agents_using = db.execute(text(
        "SELECT name FROM agents WHERE agent_break_id = :id"
    ), {"id": break_id}).fetchall()

    if agents_using:
        names = [row[0] for row in agents_using]
        raise HTTPException(
            status_code=400,
            detail=f"Break time is assigned to: {', '.join(names)}"
        )

    db.execute(text("DELETE FROM break_times WHERE id = :id"), {"id": break_id})
    db.commit()
    return {"status": "deleted", "id": break_id}
