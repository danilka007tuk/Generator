# main.py — FastAPI-сервер. Бот запускается отдельно через bot.py
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, get_session
from models import Booking
from schemas import SiteContent

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

templates = Jinja2Templates(directory="templates")

# Хранилище сгенерированного контента (в памяти)
sites_content: dict[str, SiteContent] = {}

# Хранилище подписчиков в памяти
subscribers: set[int] = set()


class Subscriber(BaseModel):
    user_id: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Только инициализация БД. Бот больше НЕ запускается здесь.
    await init_db()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return {"message": "Сервер работает. Бот запущен отдельно."}


@app.post("/api/subscribe")
async def subscribe(sub: Subscriber):
    """Регистрирует подписчика — того, кто нажал /start у бота."""
    subscribers.add(sub.user_id)
    print(f"DEBUG: подписчик {sub.user_id} зарегистрирован. Всего: {len(subscribers)}")
    return {"ok": True, "total": len(subscribers)}


@app.get("/api/health")
async def health():
    return {"status": "ok", "subscribers": len(subscribers)}


@app.get("/site/{site_id}", response_class=HTMLResponse)
async def show_site(site_id: str, request: Request):
    content = sites_content.get(site_id)
    if not content:
        return HTMLResponse("Сайт не найден", status_code=404)
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "site": content, "site_id": site_id},
    )


@app.get("/api/busy/{site_id}")
async def get_busy(site_id: str, session: AsyncSession = Depends(get_session)):
    """Возвращает список занятых слотов (дата+время)."""
    result = await session.execute(
        select(Booking.date, Booking.time).where(Booking.date >= "2026-01-01")
    )
    busy = [f"{row.date}T{row.time}" for row in result.all()]
    return {"busy": busy}


@app.post("/api/book/{site_id}")
async def create_booking(
    site_id: str,
    data: dict,
    session: AsyncSession = Depends(get_session),
):
    """Принимает новую бронь."""
    date = data.get("date")
    time = data.get("time")
    service = data.get("service")
    name = data.get("name")
    phone = data.get("phone")
    comment = data.get("comment", "")

    if not all([date, time, service, name, phone]):
        return {"ok": False, "error": "Не все поля заполнены"}

    existing = await session.execute(
        select(Booking).where(Booking.date == date, Booking.time == time)
    )
    if existing.scalar_one_or_none():
        return {"ok": False, "error": "Это время уже занято"}

    booking = Booking(
        date=date, time=time, service=service,
        name=name, phone=phone, comment=comment,
    )
    session.add(booking)
    await session.commit()

    # Уведомляем всех подписчиков
    text = (
        f"🔔 Новая бронь!\n\n"
        f"📅 {date} в {time}\n"
        f"💼 Услуга: {service}\n"
        f"👤 {name}\n"
        f"📞 {phone}\n"
        f"💬 {comment or '—'}"
    )

    # Отправка уведомлений — вызываем MAX API напрямую
    await send_notifications(text)

    return {"ok": True}


async def send_notifications(text: str):
    """Отправляет уведомление в MAX всем подписчикам + админу."""
    import os
    import httpx

    token = os.getenv("MAX_BOT_TOKEN")
    admin_id = os.getenv("MAX_ADMIN_ID")
    url = "https://platform-api2.max.ru/messages"

    recipients = set(subscribers)
    if admin_id:
        recipients.add(int(admin_id))

    async with httpx.AsyncClient() as client:
        for user_id in recipients:
            try:
                await client.post(
                    url,
                    params={"user_id": user_id},
                    headers={"Authorization": token},
                    json={"text": text},
                    timeout=10,
                )
            except Exception as e:
                print(f"Не удалось отправить уведомление {user_id}: {e}")