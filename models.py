# models.py
from sqlalchemy import String, DateTime, Boolean, UniqueConstraint, Integer
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from database import Base


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    master: Mapped[str] = mapped_column(String(100), default="")
    date: Mapped[str] = mapped_column(String(10), index=True)
    time: Mapped[str] = mapped_column(String(5))
    service: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(30))
    email: Mapped[str] = mapped_column(String(200), default="")
    comment: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("site_id", "master", "date", "time", name="uq_booking_site_master_datetime"),
    )


class Subscriber(Base):
    __tablename__ = "subscribers"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(String(100), primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class ClientContact(Base):
    __tablename__ = "client_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    email: Mapped[str] = mapped_column(String(200), index=True)
    password_hash: Mapped[str] = mapped_column(String(200), default="")
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("site_id", "email", name="uq_client_contact_site_email"),
    )


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    owner_id: Mapped[int] = mapped_column(Integer, default=0)
    content_json: Mapped[str] = mapped_column(String, default="{}")
    admin_email: Mapped[str] = mapped_column(String(200), default="")
    admin_password: Mapped[str] = mapped_column(String(200), default="")


class Master(Base):
    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(String(500), default="")
    services: Mapped[str] = mapped_column(String(500), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(200), default="")

    __table_args__ = (
        UniqueConstraint("site_id", "email", name="uq_master_site_email"),
    )


class MasterSchedule(Base):
    __tablename__ = "master_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(Integer, index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[str] = mapped_column(String(5), default="10:00")
    end_time: Mapped[str] = mapped_column(String(5), default="20:00")

    __table_args__ = (
        UniqueConstraint("master_id", "weekday", name="uq_master_weekday"),
    )