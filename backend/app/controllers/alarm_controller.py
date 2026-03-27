from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.schemas.alarm_schema import AlarmOut, AlarmCreate, AlarmUpdate
from app.services.alarm_service import AlarmService

router = APIRouter(prefix="/alarms", tags=["Alarm Records"])
service = AlarmService()

@router.get("/", response_model=list[AlarmOut])
def get_alarms(skip: int = 0, limit: int = 100, project_id: int | None = None, db: Session = Depends(get_db)):
    return service.get_alarms(db, skip, limit, project_id=project_id)

# @router.post("/", response_model=AlarmOut)
@router.post("/", response_model=AlarmOut)
def create_alarm(alarm: AlarmCreate, db: Session = Depends(get_db)):
    new_alarm = service.create_alarm(db, alarm)

    return new_alarm

@router.put("/{alarm_id}", response_model=AlarmOut)
def update_alarm(alarm_id: int, alarm: AlarmUpdate, db: Session = Depends(get_db)):
    updated = service.update_alarm(db, alarm_id, alarm)
    if not updated:
        raise HTTPException(status_code=404, detail="Alarm not found")
    return updated

@router.delete("/{alarm_id}")
def delete_alarm(alarm_id: int, db: Session = Depends(get_db)):
    success = service.delete_alarm(db, alarm_id)
    if not success:
        raise HTTPException(status_code=404, detail="Alarm not found")
    return {"status": "success"}
