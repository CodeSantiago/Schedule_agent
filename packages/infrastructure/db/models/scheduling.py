"""Barbers, services, weekly availability schedules and date-specific overrides.

Date-specific overrides come in two flavours:

- `BarberAbsence`   — the barber is NOT available on that date (vacation, sick day,
  personal errand). The absence spans the whole day unless a time range is given.
- `BarberExtraHour` — the barber IS available outside the weekly schedule on that
  date (e.g. picking up a Saturday shift). Implemented as an extra time range.

The weekly schedule remains the base layer; absences/extra-hours are applied on top
of it by the domain layer.
"""

from __future__ import annotations

from datetime import date, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.sqltypes import Boolean, Enum as SAEnum

from packages.infrastructure.db.base import Base, CreatedAt, UpdatedAt, UuidFK, UuidPK

if TYPE_CHECKING:
    from packages.infrastructure.db.models.appointments import Appointment
    from packages.infrastructure.db.models.tenants import Tenant


# Standard weekday values (ISO 8601: Monday=0 ... Sunday=6).
WEEKDAY_VALUES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WeekdayEnum: SAEnum = SAEnum(
    *WEEKDAY_VALUES,
    name="weekday",
    native_enum=True,
    create_type=False,
    create_constraint=True,
)


class Barber(Base):
    __tablename__ = "barbers"

    id: Mapped[UuidPK]

    tenant_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Free-form tag for restrictions like haircut-only (legacy domain rule).
    restrictions: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    schedules: Mapped[list["BarberSchedule"]] = relationship(
        back_populates="barber", cascade="all, delete-orphan"
    )
    absences: Mapped[list["BarberAbsence"]] = relationship(
        back_populates="barber", cascade="all, delete-orphan"
    )
    extra_hours: Mapped[list["BarberExtraHour"]] = relationship(
        back_populates="barber", cascade="all, delete-orphan"
    )
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="barber")


class Service(Base):
    __tablename__ = "services"

    id: Mapped[UuidPK]

    tenant_id: Mapped[UuidFK] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Legacy short code (C / B / CB) used by the booking domain to enforce
    # rules like haircut-only and the 2-slot CB invariant. Defaults to
    # "OTHER" for newly-added services that have not been classified yet.
    code: Mapped[str] = mapped_column(
        String(8), nullable=False, default="OTHER", server_default="OTHER"
    )
    # Duration in minutes. HAIRCUT_AND_BEARD in legacy = 60.
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Price in minor units (cents) to avoid float math.
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class BarberSchedule(Base):
    """Weekly recurring availability for a barber.

    Date-specific overrides (absences / extra hours) live in their own tables
    and are merged on top of this base layer by the domain logic.
    """

    __tablename__ = "barber_schedules"
    __table_args__ = (
        UniqueConstraint("barber_id", "weekday", "start_time", name="uq_barber_schedule_slot"),
    )

    id: Mapped[UuidPK]

    barber_id: Mapped[UuidFK] = mapped_column(
        ForeignKey("barbers.id", ondelete="CASCADE"),
        nullable=False,
    )
    weekday: Mapped[str] = mapped_column(WeekdayEnum, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    barber: Mapped["Barber"] = relationship(back_populates="schedules")


# --- Date-specific overrides -------------------------------------------------


class BarberAbsence(Base):
    """A barber is unavailable on a specific date (vacation, sick day, etc.).

    If `start_time` / `end_time` are both NULL, the absence covers the whole day.
    Otherwise it covers the given time range on `absence_date`.
    """

    __tablename__ = "barber_absences"

    id: Mapped[UuidPK]

    barber_id: Mapped[UuidFK] = mapped_column(
        ForeignKey("barbers.id", ondelete="CASCADE"),
        nullable=False,
    )
    absence_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Optional: partial-day absence. If both are NULL → whole day off.
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    barber: Mapped["Barber"] = relationship(back_populates="absences")


class BarberExtraHour(Base):
    """Extra availability outside the weekly schedule for a specific date.

    Used for date-specific additions like "this Saturday I'll work 10-14".
    Multiple extra-hour rows per barber/date are allowed; the domain layer
    merges them with the weekly schedule.
    """

    __tablename__ = "barber_extra_hours"

    id: Mapped[UuidPK]

    barber_id: Mapped[UuidFK] = mapped_column(
        ForeignKey("barbers.id", ondelete="CASCADE"),
        nullable=False,
    )
    extra_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    barber: Mapped["Barber"] = relationship(back_populates="extra_hours")
