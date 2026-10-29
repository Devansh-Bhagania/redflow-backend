import json
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.models.integration import IntegrationConnection
from app.schemas.integration import ConnectEmailRequest, ConnectWhatsAppRequest


async def connect_whatsapp(
    db: AsyncSession, organization_id: uuid.UUID, payload: ConnectWhatsAppRequest
) -> IntegrationConnection:
    existing = await db.execute(
        select(IntegrationConnection).where(IntegrationConnection.external_id == payload.phone_number_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "This WhatsApp number is already connected")

    credentials = {"phone_number_id": payload.phone_number_id, "access_token": payload.access_token, "app_secret": payload.app_secret}
    connection = IntegrationConnection(
        organization_id=organization_id,
        provider="whatsapp",
        external_id=payload.phone_number_id,
        credentials_encrypted=encrypt(json.dumps(credentials)),
        status="ACTIVE",
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)
    return connection


async def connect_email(db: AsyncSession, organization_id: uuid.UUID, payload: ConnectEmailRequest) -> IntegrationConnection:
    existing = await get_connection_for_org(db, organization_id, "email")
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "An email connection already exists for this organization")

    connection = IntegrationConnection(
        organization_id=organization_id,
        provider="email",
        external_id=payload.from_email,
        credentials_encrypted=encrypt(json.dumps(payload.model_dump())),
        status="ACTIVE",
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)
    return connection


async def get_connection_for_org(db: AsyncSession, organization_id: uuid.UUID, provider: str) -> IntegrationConnection | None:
    result = await db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.organization_id == organization_id,
            IntegrationConnection.provider == provider,
            IntegrationConnection.status == "ACTIVE",
        )
    )
    return result.scalar_one_or_none()


async def get_whatsapp_connection_for_org(db: AsyncSession, organization_id: uuid.UUID) -> IntegrationConnection | None:
    return await get_connection_for_org(db, organization_id, "whatsapp")


async def get_whatsapp_connection_by_phone_number_id(db: AsyncSession, phone_number_id: str) -> IntegrationConnection | None:
    result = await db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.external_id == phone_number_id,
            IntegrationConnection.provider == "whatsapp",
            IntegrationConnection.status == "ACTIVE",
        )
    )
    return result.scalar_one_or_none()


def get_credentials(connection: IntegrationConnection) -> dict:
    return json.loads(decrypt(connection.credentials_encrypted))
