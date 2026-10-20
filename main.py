# main.py — FastAPI-сервер. Бот запускается отдельно через bot.py
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import date as date_type

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import httpx
import resend
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, get_session
from models import Booking, Subscriber
from schemas import SiteContent

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

logger = logging.getLogger("uvicorn.error")
templates = Jinja2Templates(directory="templates")

sites_content: dict[str, SiteContent] = {}


class SubscriberRequest(BaseModel):
    user_id: int
    site_id: str


class BookingRequest(BaseModel):
    date: str
    time: str = "10:00"
    service: str
    name: str
    phone: str
    email: str = ""        # <--- добавили email
    comment: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return {"message": "Сервер работает. Бот запущен отдельно."}


@app.post("/api/subscribe")
async def subscribe(
    sub: SubscriberRequest,
    session: AsyncSession = Depends(get_session),
):
    """Регистрирует подписчика конкретного сайта."""
    existing = await session.get(Subscriber, (sub.user_id, sub.site_id))
    if not existing:
        session.add(Subscriber(user_id=sub.user_id, site_id=sub.site_id))
        await session.commit()

    result = await session.execute(
        select(Subscriber).where(Subscriber.site_id == sub.site_id)
    )
    total = len(result.scalars().all())
    logger.info(f"Подписчик {sub.user_id} → {sub.site_id}. Всего на сайте: {total}")
    return {"ok": True, "total": total}


@app.get("/api/health")
async def health(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Subscriber))
    total = len(result.scalars().all())
    return {"status": "ok", "subscribers": total}


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
async def get_busy(
    site_id: str,
    session: AsyncSession = Depends(get_session),
):
    today = date_type.today().isoformat()
    result = await session.execute(
        select(Booking.date, Booking.time).where(
            Booking.site_id == site_id,
            Booking.date >= today,
        )
    )
    busy = [f"{row.date}T{row.time}" for row in result.all()]
    return {"busy": busy}


@app.post("/api/book/{site_id}")
async def create_booking(
    site_id: str,
    data: BookingRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Принимает новую бронь."""
    existing = await session.execute(
        select(Booking).where(
            Booking.site_id == site_id,
            Booking.date == data.date,
            Booking.time == data.time,
        )
    )
    if existing.scalar_one_or_none():
        return {"ok": False, "error": "Это время уже занято"}

    booking = Booking(
        site_id=site_id,
        date=data.date,
        time=data.time,
        service=data.service,
        name=data.name,
        phone=data.phone,
        comment=data.comment,
    )
    session.add(booking)
    await session.commit()

    # Текст для MAX
    text = (
        f"🔔 Новая бронь!\n\n"
        f"🌐 Сайт: {site_id}\n"
        f"📅 {data.date} в {data.time}\n"
        f"💼 Услуга: {data.service}\n"
        f"👤 {data.name}\n"
        f"📞 {data.phone}\n"
        f"📧 {data.email or '—'}\n"
        f"💬 {data.comment or '—'}"
    )

    # 1. MAX-уведомление
    background_tasks.add_task(send_notifications, text, site_id, session)

    # 2. Email-уведомление (Resend)
    background_tasks.add_task(send_email_resend, site_id, data)

    return {"ok": True}


async def send_notifications(text: str, site_id: str, session: AsyncSession):
    """Отправляет уведомление в MAX: подписчикам сайта + админу."""
    token = os.getenv("MAX_BOT_TOKEN")
    admin_id = os.getenv("MAX_ADMIN_ID")
    url = "https://platform-api2.max.ru/messages"

    result = await session.execute(
        select(Subscriber.user_id).where(Subscriber.site_id == site_id)
    )
    recipients = {row.user_id for row in result.all()}

    if admin_id:
        try:
            recipients.add(int(admin_id))
        except ValueError:
            logger.warning(f"MAX_ADMIN_ID не число: {admin_id}")

    if not recipients:
        logger.warning(f"Нет получателей для {site_id}")
        return

    logger.info(f"Рассылка [{site_id}] → {len(recipients)} получателям")

    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        for user_id in recipients:
            try:
                response = await client.post(
                    url,
                    params={"user_id": user_id},
                    headers={"Authorization": token},
                    json={"text": text},
                    timeout=15,
                )
                if response.status_code != 200:
                    logger.warning(f"MAX {response.status_code} для {user_id}: {response.text[:200]}")
                else:
                    logger.info(f"Уведомление отправлено {user_id}")
            except Exception as e:
                logger.error(f"Не удалось отправить {user_id}: {e}")


async def send_email_resend(site_id: str, data: BookingRequest):
    """Отправляет email клиенту (если указан email)."""
    resend.api_key = os.getenv("RESEND_API_KEY")
    if not resend.api_key:
        logger.warning("RESEND_API_KEY не задан — email не отправлен")
        return

    if not data.email:
        logger.info("Клиент не указал email — пропускаем")
        return

    try:
        params = {
            "from": "onboarding@resend.dev",
            "to": [data.email],
            "subject": f"Подтверждение записи на {data.service}",
            "html": f"""
            <h2>Ваша заявка принята!</h2>
            <p><strong>Сайт:</strong> {site_id}</p>
            <p><strong>Услуга:</strong> {data.service}</p>
            <p><strong>Дата:</strong> {data.date} в {data.time}</p>
            <p><strong>Имя:</strong> {data.name}</p>
            <p><strong>Телефон:</strong> {data.phone}</p>
            <p>Спасибо! Мы свяжемся с вами для подтверждения.</p>
            """
        }
        response = resend.Emails.send(params)
        logger.info(f"Email отправлен {data.email}. ID: {response.get('id')}")
    except Exception as e:
        logger.error(f"Ошибка отправки email: {e}")