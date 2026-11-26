from sqlalchemy import JSON, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base


class VersionBatch(Base):
    """One two-denoiser comparison lifecycle on a fixed dual-score ledger."""

    __tablename__ = 'version_batches'

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(140))
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('accounts.id'))
    created_at: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), index=True)
    holdout_fraction: Mapped[float] = mapped_column(Float)
    withdrawn_ids: Mapped[list] = mapped_column(JSON, default=list)
    gate: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded_by: Mapped[int | None] = mapped_column(ForeignKey('version_batches.id'), nullable=True)
    recompute_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class VersionSignoff(Base):
    """Admin sign-off of a switch recommendation; history is never deleted."""

    __tablename__ = 'version_signoffs'

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('version_batches.id'), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey('accounts.id'))
    decision: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), index=True)
    recommendation_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(32))
    superseded_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
