import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification


async def create_notification(
    db: AsyncSession, organization_id: uuid.UUID, title: str, body: str, user_id: uuid.UUID | None = None
) -> Notification:
    notification = Notification(organization_id=organization_id, user_id=user_id, title=title, body=body)
    db.add(notification)
    await db.commit()
    await db.refresh(notification)
    return notification


async def list_notifications(db: AsyncSession, organization_id: uuid.UUID) -> list[Notification]:
    result = await db.execute(
        select(Notification)
        .where(Notification.organization_id == organization_id)
        .order_by(Notification.created_at.desc())
    )
    return list(result.scalars().all())
