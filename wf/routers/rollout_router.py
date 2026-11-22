# coding: utf-8
# 2026-11-17: wdrożenia obiegów (wf/services/rollouts.py).
"""
Wdrożenia obiegów - tryb użycia obiegu w jednostkach organizacyjnych.
  * `GET /process-rollouts[?process_model_id=&active_on=]` - rejestr wdrożeń,
  * `GET /process-rollouts/options/process-models` - opcje wyboru obiegu
    (formularz "Wdrożenie obiegów"; value = klucz modelu),
  * `GET /process-rollouts/options/org-units` - opcje wyboru jednostki
    (value = id jednostki).

Wdrożenia powstają wyłącznie z aktu (krok `obiegi.wdrozenie`), więc API
nie ma operacji zapisu.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user
from wf.models import OrgUnit, ProcessModel, ProcessRollout
from wf.schemas.wf_schemas import CurrentUser, OptionRead, ProcessRolloutRead

router = APIRouter(prefix="/process-rollouts", tags=["process-rollouts"])


@router.get("", response_model=list[ProcessRolloutRead])
def list_rollouts(process_model_id: UUID | None = None, active_on: date | None = None,
                  db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(get_current_wf_user)):
    stmt = select(ProcessRollout).order_by(ProcessRollout.valid_from.desc(),
                                           ProcessRollout.created_at.desc())
    if process_model_id is not None:
        stmt = stmt.where(ProcessRollout.process_model_id == process_model_id)
    rows = db.execute(stmt).scalars().all()
    if active_on is not None:
        rows = [r for r in rows if r.valid_from <= active_on
                and (r.valid_to is None or active_on < r.valid_to)]
    return rows


@router.get("/options/process-models", response_model=list[OptionRead])
def process_model_options(q: str | None = Query(default=None, max_length=200),
                          limit: int = Query(default=50, ge=1, le=500),
                          db: Session = Depends(get_db),
                          current_user: CurrentUser = Depends(get_current_wf_user)):
    stmt = select(ProcessModel).where(ProcessModel.is_active.is_(True)).order_by(ProcessModel.name)
    if q:
        stmt = stmt.where(ProcessModel.name.ilike(f"%{q}%") | ProcessModel.key.ilike(f"%{q}%"))
    return [OptionRead(value=m.key, label=f"{m.name} ({m.key})")
            for m in db.execute(stmt.limit(limit)).scalars()]


@router.get("/options/org-units", response_model=list[OptionRead])
def org_unit_options(q: str | None = Query(default=None, max_length=200),
                     limit: int = Query(default=50, ge=1, le=500),
                     db: Session = Depends(get_db),
                     current_user: CurrentUser = Depends(get_current_wf_user)):
    stmt = select(OrgUnit).order_by(OrgUnit.name)
    if q:
        stmt = stmt.where(OrgUnit.name.ilike(f"%{q}%") | OrgUnit.code.ilike(f"%{q}%"))
    return [OptionRead(value=str(u.id), label=f"{u.name} ({u.code})")
            for u in db.execute(stmt.limit(limit)).scalars()]
