from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class EvmSnapshot(Base):
    """Formularz 11 — raport EVM; jeden wiersz na dzien raportowania (historia -> S-krzywa)."""

    __tablename__ = "pm_evm_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("pm_projects.id", ondelete="CASCADE"))
    report_date: Mapped[date] = mapped_column(Date)
    bac: Mapped[float] = mapped_column(Float)
    pv: Mapped[float] = mapped_column(Float)
    ev: Mapped[float] = mapped_column(Float)
    ac: Mapped[float] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
