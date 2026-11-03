from datetime import date

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class Milestone(Base):
    """Formularz 12 — kamien milowy sledzony w Analizie Trendu Kamieni Milowych (MTA)."""

    __tablename__ = "pm_milestones"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("pm_projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    baseline_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class MilestoneForecast(Base):
    """Kolejne prognozy terminu danego kamienia milowego w czasie (jeden wiersz wykresu MTA)."""

    __tablename__ = "pm_milestone_forecasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    milestone_id: Mapped[int] = mapped_column(
        ForeignKey("pm_milestones.id", ondelete="CASCADE")
    )
    report_date: Mapped[date] = mapped_column(Date)
    forecast_date: Mapped[date] = mapped_column(Date)
