"""Appointments (booked time slots).

A booked appointment is uniquely identified by the tuple
(tenant_id, barber_id, appointment_date, start_time) — that uniqueness
constraint is the hard guarantee that no double-booking happens for the
same slot.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.sqltypes import Enum as SAEnum

from packages.infrastructure.db.base import Base, CreatedAt, UpdatedAt, UuidFK, UuidPK

if TYPE_CHECKING:
    from packages.infrastructure.db.models.scheduling import Barber, Service
    from packages.infrastructure.db.models.tenants import Tenant


APPOINTMENT_STATUS_VALUES = (
    "pending",    # booked by bot, awaiting confirmation
    "confirmed",  # confirmed by barber/customer
    "cancelled",  # cancelled before start
    "completed",  # service rendered
    "no_show",    # customer did not show up
)
AppointmentStatusEnum: SAEnum = SAEnum(
    *APPOINTMENT_STATUS_VALUES,
    name="appointment_status",
    native_enum=True,
    create_type=False,
    create_constraint=True,
)


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        # Hard guard against double-booking a slot.
        UniqueConstraint(
            "tenant_id",
            "barber_id",
            "appointment_date",
            "start_time",
            name="uq_appointment_slot",
        ),
    )

    id: Mapped[UuidPK]

    tenant_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    barber_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("barbers.id", ondelete="CASCADE"),
        nullable=False,
    )
    service_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("services.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Date of the appointment in the tenant's local timezone.
    appointment_date: Mapped[date] = mapped_column(nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[str] = mapped_column(
        AppointmentStatusEnum,
        nullable=False,
        default="pending",
        server_default="pending",
    )
    customer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Configurable identity fields (optional, driven by tenant
    # ``booking.customer_identity_mode``). When NULL the display
    # ``customer_name`` is the canonical identity.
    customer_dni: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_last_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    barber: Mapped["Barber"] = relationship(back_populates="appointments")
    # `service` and `tenant` relationships are intentionally lazy; load on access.
