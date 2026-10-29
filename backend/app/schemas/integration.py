from pydantic import BaseModel


class ConnectWhatsAppRequest(BaseModel):
    phone_number_id: str
    access_token: str
    app_secret: str


class ConnectEmailRequest(BaseModel):
    from_email: str
    smtp_host: str
    smtp_port: int = 587
    smtp_username: str
    smtp_password: str


class IntegrationConnectionOut(BaseModel):
    provider: str
    external_id: str
    status: str

    model_config = {"from_attributes": True}
