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
    """Контакты клиентов сайта (email + верификация)."""
    __tablename__ = "client_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)
    email: Mapped[str] = mapped_column(String(200), index=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_token: Mapped[str] = mapped_column(String(200), nullable=True, index=True)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class Site(Base):
    """Хранит сгенерированный контент сайта для редактирования через дашборд."""
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(String(100), primary_key=True) # site_id
    owner_id: Mapped[int] = mapped_column(Integer, default=0) # ID пользователя MAX
    content_json: Mapped[str] = mapped_column(String, default="{}") # Весь контент в JSON