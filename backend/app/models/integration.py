from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrgScopedMixin, TimestampMixin, UUIDPKMixin


class IntegrationConnection(Base, UUIDPKMixin, OrgScopedMixin, TimestampMixin):
    __tablename__ = "integration_connections"

    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "whatsapp"
    # public lookup key for inbound webhooks, e.g. the WhatsApp phone_number_id.
    # Never store secrets here in the clear — those go in credentials_encrypted.
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True, unique=True)
    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
