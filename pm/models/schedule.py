from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class WbsItem(Base):
    """Formularz 07 — WBS + slownik WBS + RACI (jedno wiersz = jeden wezel WBS)."""

    __tablename__ = "pm_wbs_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("pm_projects.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(32))
    parent_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    level: Mapped[int] = mapped_column(Integer, default=1)
    deliverable: Mapped[str | None] = mapped_column(Text, nullable=True)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    responsible: Mapped[str | None] = mapped_column(String(255), nullable=True)
    accountable: Mapped[str | None] = mapped_column(String(255), nullable=True)
    consulted: Mapped[str | None] = mapped_column(String(255), nullable=True)
    informed: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ScheduleTask(Base):
    """Formularze 07/09/10 — siec CPM oraz estymaty PERT dla tej samej listy zadan.

    `predecessors` to lista obiektow: [{"code": "A", "type": "FS", "lag": 0}, ...]
    (typ zaleznosci: FS/SS/FF/SF, zgodnie z rozdz. 3 PDM).
    `optimistic`/`most_likely`/`pessimistic` sluza PERT; gdy podano tylko
    `most_likely` (lub `duration`), zadanie jest traktowane deterministycznie (CPM).
    """

    __tablename__ = "pm_schedule_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("pm_projects.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(32))
    wbs_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    predecessors: Mapped[list] = mapped_column(JSON, default=list)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    optimistic: Mapped[float | None] = mapped_column(Float, nullable=True)
    most_likely: Mapped[float | None] = mapped_column(Float, nullable=True)
    pessimistic: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_critical_chain: Mapped[bool] = mapped_column(default=False)
