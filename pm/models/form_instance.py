from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class FormInstance(Base):
    """Generic storage for all 20 formularze/*.md documents.

    Structured, calculation-critical forms additionally materialize rows into
    dedicated tables (WbsItem, ScheduleTask, Risk, EvmSnapshot, ...); this table
    remains the single source for the narrative/checklist-only forms.
    """

    __tablename__ = "pm_form_instances"
    __table_args__ = (UniqueConstraint("project_id", "form_slug", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("pm_projects.id", ondelete="CASCADE"))
    form_slug: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
