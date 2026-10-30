from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def utc_now() -> datetime:
    """
    Возвращает текущее время UTC.

    Текущая база использует DateTime без отдельного timezone-типа,
    поэтому структура существующей таблицы не меняется.
    """
    return datetime.now(timezone.utc)


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    site_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    master: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )

    date: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        index=True,
    )

    time: Mapped[str] = mapped_column(
        String(5),
        nullable=False,
    )

    service: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    phone: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    email: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )

    comment: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )

    __table_args__ = (
        UniqueConstraint(
            "site_id",
            "master",
            "date",
            "time",
            name="uq_booking_site_master_datetime",
        ),
        Index(
            "ix_bookings_site_date_time",
            "site_id",
            "date",
            "time",
        ),
        Index(
            "ix_bookings_site_email",
            "site_id",
            "email",
        ),
    )


class Subscriber(Base):
    __tablename__ = "subscribers"

    user_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    site_id: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )


class ClientContact(Base):
    __tablename__ = "client_contacts"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    site_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    email: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
    )

    password_hash: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )

    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )

    __table_args__ = (
        UniqueConstraint(
            "site_id",
            "email",
            name="uq_client_contact_site_email",
        ),
        Index(
            "ix_client_contacts_site_verified",
            "site_id",
            "is_verified",
        ),
    )


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
    )

    owner_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    content_json: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="{}",
    )

    admin_email: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )

    admin_password: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )


class Master(Base):
    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    site_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )

    description: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )

    services: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    email: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )

    password_hash: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )

    __table_args__ = (
        UniqueConstraint(
            "site_id",
            "email",
            name="uq_master_site_email",
        ),
        Index(
            "ix_masters_site_active",
            "site_id",
            "is_active",
        ),
    )


class MasterSchedule(Base):
    __tablename__ = "master_schedules"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    master_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    weekday: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    start_time: Mapped[str] = mapped_column(
        String(5),
        nullable=False,
        default="10:00",
    )

    end_time: Mapped[str] = mapped_column(
        String(5),
        nullable=False,
        default="20:00",
    )

    __table_args__ = (
        UniqueConstraint(
            "master_id",
            "weekday",
            name="uq_master_weekday",
        ),
    )