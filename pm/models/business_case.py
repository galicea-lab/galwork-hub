from sqlalchemy import JSON, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class BusinessCaseVariant(Base):
    """Formularz 03 — warianty Business Case (0/A/B) do porownania NPV/ROI/payback."""

    __tablename__ = "pm_business_case_variants"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("pm_projects.id", ondelete="CASCADE"))
    variant_name: Mapped[str] = mapped_column(String(64))
    discount_rate: Mapped[float] = mapped_column(Float, default=0.08)
    cashflows: Mapped[list] = mapped_column(JSON, default=list)  # index 0 = rok 0 (inwestycja)
