from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AuditEventItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    actor_user_id: int
    event_type: str
    target_type: str
    target_id: int
    outcome: str
    event_data: dict
    created_at: datetime


class AuditEventPage(BaseModel):
    items: list[AuditEventItem]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=100_000)
    total: int
