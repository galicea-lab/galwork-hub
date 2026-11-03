from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class DpciScore(Base):
    """Formularz 06 — pojedyncza ocena atrybutu DPCI (Darnall-Preston Complexity Index)."""

    __tablename__ = "pm_dpci_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("pm_projects.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(128))
    attribute: Mapped[str] = mapped_column(String(255))
    score: Mapped[int] = mapped_column(Integer)  # 1..5
    assessed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
