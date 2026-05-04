from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import Analysis, AnalysisSource, User


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    full_name: str | None = None,
) -> User:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is not None:
        changed = False
        if username and user.username != username:
            user.username = username
            changed = True
        if full_name and user.full_name != full_name:
            user.full_name = full_name
            changed = True
        if changed:
            await session.flush()
        return user

    user = User(telegram_id=telegram_id, username=username, full_name=full_name)
    session.add(user)
    await session.flush()
    return user


async def list_user_analyses(
    session: AsyncSession,
    user_id: int,
    limit: int = 10,
    offset: int = 0,
) -> list[Analysis]:
    result = await session.execute(
        select(Analysis)
        .where(Analysis.user_id == user_id)
        .order_by(desc(Analysis.created_at))
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def save_analysis(
    session: AsyncSession,
    user_id: int,
    source: AnalysisSource,
    raw_input: str | None,
    ingredients: list,
    result_payload: dict,
    risk_score: float,
    product_title: str | None = None,
) -> Analysis:
    analysis = Analysis(
        user_id=user_id,
        source=source,
        raw_input=raw_input,
        ingredients=ingredients,
        result=result_payload,
        risk_score=risk_score,
        product_title=product_title,
    )
    session.add(analysis)
    await session.flush()
    return analysis
