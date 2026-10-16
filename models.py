# models.py
from sqlalchemy import String, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from database import Base


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(String(100), index=True)   # репозиторий
    date: Mapped[str] = mapped_column(String(10), index=True)       # YYYY-MM-DD
    time: Mapped[str] = mapped_column(String(5))                     # HH:MM
    service: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("site_id", "date", "time", name="uq_booking_site_datetime"),
    )


class Subscriber(Base):
    __tablename__ = "subscribers"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(String(100), primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )