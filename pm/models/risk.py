from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class Risk(Base):
    """Formularz 08 — rejestr ryzyka + EMV."""

    __tablename__ = "pm_risks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("pm_projects.id", ondelete="CASCADE"))
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    risk_type: Mapped[str] = mapped_column(String(1), default="Z")  # Z = zagrozenie, S = szansa
    probability: Mapped[float] = mapped_column(Float)  # 0..1
    impact: Mapped[float] = mapped_column(Float)  # PLN; dodatni=strata, ujemny=zysk
    strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
