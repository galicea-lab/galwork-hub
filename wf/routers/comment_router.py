# coding: utf-8

"""Router API dla komentarzy do instancji procesu.

(2026-10-05): autor komentarza to zalogowany użytkownik z tokenu Keycloak 
(`get_current_wf_user` -> `users.id`), a nie podawany w zapytaniu UUID.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from api.dependencies import get_db
from wf.dependencies import get_current_wf_user
from wf.models import Comment, ProcessInstance
from wf.schemas.wf_schemas import CommentCreate, CommentRead, CurrentUser

router = APIRouter(prefix="/process-instances/{instance_id}/comments", tags=["comments"])


@router.get("", response_model=list[CommentRead])
def list_comments(instance_id: UUID,
                  db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(get_current_wf_user)):
    if db.get(ProcessInstance, instance_id) is None:
        raise HTTPException(status_code=404, detail="Instancja procesu nie znaleziona")
    stmt = (select(Comment)
            .options(selectinload(Comment.author))
            .where(Comment.process_instance_id == instance_id)
            .order_by(Comment.created_at))
    return db.execute(stmt).scalars().all()


@router.post("", response_model=CommentRead, status_code=201)
def create_comment(instance_id: UUID, payload: CommentCreate,
                    db: Session = Depends(get_db),
                    current_user: CurrentUser = Depends(get_current_wf_user)):
    if db.get(ProcessInstance, instance_id) is None:
        raise HTTPException(status_code=404, detail="Instancja procesu nie znaleziona")
    comment = Comment(
        process_instance_id=instance_id,
        human_task_id=payload.human_task_id,
        document_id=payload.document_id,
        author_id=current_user.id,
        body=payload.body,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment
