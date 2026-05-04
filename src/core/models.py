from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.db import Base
from src.core.enums import AnalysisSource, SkinType

__all__ = [
    "AnalysisSource",
    "SkinType",
    "User",
    "Analysis",
]


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    skin_type: Mapped[SkinType] = mapped_column(
        Enum(
            SkinType,
            name="skin_type",
            values_callable=lambda e: [m.value for m in e],
            create_type=False,
        ),
        default=SkinType.UNKNOWN,
    )
    language: Mapped[str] = mapped_column(String(8), default="ru")
    is_banned: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[AnalysisSource] = mapped_column(
        Enum(
            AnalysisSource,
            name="analysis_source",
            values_callable=lambda e: [m.value for m in e],
            create_type=False,
        )
    )
    raw_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ingredients: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user: Mapped["User"] = relationship(back_populates="analyses")

    __table_args__ = (
        Index("ix_analyses_user_created", "user_id", "created_at"),
    )
