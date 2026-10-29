import uuid

from pydantic import BaseModel, EmailStr


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str


class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    invited_email: str | None
    role: str
    status: str

    model_config = {"from_attributes": True}
