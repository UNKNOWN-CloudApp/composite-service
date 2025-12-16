from __future__ import annotations

from typing import Optional, Literal
from uuid import UUID, uuid4
from datetime import datetime
from pydantic import BaseModel, Field


class BookingBase(BaseModel):
    listing_id: int = Field(
        ...,
        description="ID of the listing being booked.",
        json_schema_extra={"example":1},
    )
    tenant_email: str = Field(
        ...,
        description="email of the tenant requesting the booking.",
        json_schema_extra={"example": "david.lee@example.com"},
    )
    
    start_date: datetime = Field(
        ...,
        description="Start date/time of the rental period (UTC).",
        json_schema_extra={"example": "2025-05-01T14:00:00Z"},
    )
    end_date: Optional[datetime] = Field(
        None,
        description="End date/time of the rental period (UTC).",
        json_schema_extra={"example": "2025-05-15T11:00:00Z"},
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "ID": 1,
                    "listing_id": 1,
                    "tenant_email": "david.lee@example.com",
                    "start_date": "2025-05-01T14:00:00Z",
                    "end_date": "2025-05-15T11:00:00Z",
                }
            ]
        }
    }


class BookingCreate(BookingBase):
    """Creation payload; ID is generated server-side but present in the base model."""

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "listing_id": 1,
                    "tenant_email": "david.lee@example.com",
                    "start_date": "2025-05-01T14:00:00Z",
                    "end_date": "2025-05-15T11:00:00Z",
                }
            ]
        }
    }


class BookingUpdate(BaseModel):
    """Partial update; booking ID is taken from the path, not the body."""
    start_date: Optional[datetime] = Field(
        None,
        description="Updated start date/time.",
        json_schema_extra={"example": "2025-05-02T10:00:00Z"},
    )
    end_date: Optional[datetime] = Field(
        None,
        description="Updated end date/time.",
        json_schema_extra={"example": "2025-05-16T11:00:00Z"},
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "start_date": "2025-05-02T10:00:00Z",
                    "end_date": "2025-05-16T11:00:00Z",
                },
            ]
        }
    }

class BookingRead(BookingBase):
    id: int

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": 1,
                    "listing_id": 1,
                    "tenant_email": "david.lee@example.com",
                    "start_date": "2025-05-01T14:00:00Z",
                    "end_date": "2025-05-15T11:00:00Z",
                }
            ]
        }
    }

    