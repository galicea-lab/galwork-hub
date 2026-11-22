# coding: utf-8
# 2026-11-17:  dyskusje o sprawie w komunikatorze (wf/services/discussions.py).
"""
  * `GET  /process-instances/{id}/discussions` - komunikator, adres
    "Rozpocznij dyskusję" i linki do dyskusji w sprawie,
  * `POST /process-instances/{id}/discussions` - dołączenie linku (permalink).
Uprawnienia jak przy dokumentach sprawy (instance_print.can_print).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user
from wf.models import ProcessInstance
from wf.schemas.wf_schemas import CurrentUser, DiscussionCreate, DiscussionList, DiscussionRead
from wf.services import discussions, instance_print

router = APIRouter(tags=["discussions"])


def _instance_for(db: Session, instance_id: UUID, user: CurrentUser) -> ProcessInstance:
    instance = instance_print.find_instance(db, instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Instancja procesu nie znaleziona")
    if not instance_print.can_print(instance, user):
        raise HTTPException(status_code=403, detail="Brak uprawnień do tej sprawy")
    return instance


@router.get("/process-instances/{instance_id}/discussions", response_model=DiscussionList)
def list_instance_discussions(instance_id: UUID, db: Session = Depends(get_db),
                              current_user: CurrentUser = Depends(get_current_wf_user)):
    instance = _instance_for(db, instance_id, current_user)
    return DiscussionList(provider=discussions.provider(), start_url=discussions.start_url(instance),
                          items=[DiscussionRead.model_validate(d)
                                 for d in discussions.list_discussions(db, instance)])


@router.post("/process-instances/{instance_id}/discussions", response_model=DiscussionRead,
             status_code=201)
def add_instance_discussion(instance_id: UUID, payload: DiscussionCreate,
                            db: Session = Depends(get_db),
                            current_user: CurrentUser = Depends(get_current_wf_user)):
    instance = _instance_for(db, instance_id, current_user)
    try:
        return discussions.add_discussion(db, instance, current_user, url=payload.url,
                                          title=payload.title, task_id=payload.task_id)
    except discussions.DiscussionError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
