# models.py
from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, timezone
from database import Base

class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String(10))       # YYYY-MM-DD
    time: Mapped[str] = mapped_column(String(5))         # HH:MM
    service: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Уникальный индекс: на одну дату+время может быть только одна бронь
    __table_args__ = (
        # Позже добавим UniqueConstraint, если нужно
    )