# main.py — FastAPI-сервер. Бот запускается отдельно через bot.py
import os
import logging
import secrets
import asyncio
import json
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import date as date_type, datetime, timedelta, timezone

import httpx
import resend
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends, BackgroundTasks, Body, Cookie, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from database import init_db, get_session, async_session
from models import Booking, Subscriber, ClientContact, Site, Master, MasterSchedule
from schemas import SiteContent

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

logger = logging.getLogger("uvicorn.error")
templates = Jinja2Templates(directory="templates")


class SubscriberRequest(BaseModel):
    user_id: int
    site_id: str


class BookingRequest(BaseModel):
    date: str
    time: str = "10:00"
    master: str = ""
    service: str
    name: str
    phone: str
    email: str = ""
    comment: str = ""


class ClientRegisterRequest(BaseModel):
    email: str


class LoginRequest(BaseModel):
    site_id: str
    email: str
    password: str = ""


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


@app.get("/")
async def index():
    return {"message": "Сервер работает. Бот запущен отдельно."}


# ============ АВТОРИЗАЦИЯ ============
@app.post("/api/login")
async def login(data: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)):
    # 1. Проверяем, админ ли это (по паролю)
    site = await session.get(Site, data.site_id)
    if site and site.admin_password:
        if hashlib.sha256(data.password.encode()).hexdigest() == site.admin_password:
            response.set_cookie(key="role", value="admin", httponly=True)
            response.set_cookie(key="site_id", value=data.site_id, httponly=True)
            return {"ok": True, "role": "admin", "redirect": "/dashboard/" + data.site_id}

    # 2. Проверяем, зарегистрирован ли клиент
    email = data.email.strip().lower()
    result = await session.execute(
        select(ClientContact).where(ClientContact.site_id == data.site_id, ClientContact.email == email)
    )
    contact = result.scalar_one_or_none()

    if contact:
        if contact.is_verified:
            response.set_cookie(key="role", value="client", httponly=True)
            response.set_cookie(key="email", value=email, httponly=True)
            response.set_cookie(key="site_id", value=data.site_id, httponly=True)
            return {"ok": True, "role": "client", "redirect": "/client_dashboard/" + data.site_id}
        else:
            return {"ok": False, "error": "Email не подтвержден. Проверьте почту."}

    return {"ok": False, "error": "Пользователь не найден. Зарегистрируйтесь."}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, site_id: str = "site"):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"site_id": site_id, "server_url": os.getenv("SERVER_URL", "https://api.generator-beloretsk.ru")}
    )


@app.get("/client_dashboard/{site_id}", response_class=HTMLResponse)
async def client_dashboard(site_id: str, request: Request):
    return templates.TemplateResponse(
        request=request,
        name="client_dashboard.html",
        context={"site_id": site_id, "server_url": os.getenv("SERVER_URL", "https://api.generator-beloretsk.ru")}
    )


@app.get("/api/my-bookings")
async def get_my_bookings(
        email: str = Cookie(None),
        site_id: str = Cookie(None),
        session: AsyncSession = Depends(get_session)
):
    if not email or not site_id:
        return {"ok": False, "error": "Не авторизован"}

    result = await session.execute(
        select(Booking).where(Booking.site_id == site_id, Booking.email == email).order_by(Booking.date.desc(),
                                                                                           Booking.time.desc())
    )
    bookings = result.scalars().all()
    return {"ok": True, "bookings": [
        {"date": b.date, "time": b.time, "master": b.master, "service": b.service, "name": b.name, "phone": b.phone}
        for b in bookings
    ]}


@app.get("/logout")
async def logout(response: Response):
    response.delete_cookie("role")
    response.delete_cookie("email")
    response.delete_cookie("site_id")
    return {"ok": True, "message": "Вы вышли"}


# ============ ДАШБОРД ============
@app.get("/dashboard/{site_id}", response_class=HTMLResponse)
async def dashboard(site_id: str, request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"site_id": site_id, "server_url": os.getenv("SERVER_URL", "https://api.generator-beloretsk.ru")}
    )


# ============ CMS (КОНТЕНТ) ============
@app.get("/api/site-content/{site_id}")
async def get_site_content(site_id: str, session: AsyncSession = Depends(get_session)):
    site = await session.get(Site, site_id)
    if not site: return {"ok": False, "error": "Сайт не найден"}
    return {"ok": True, "content": json.loads(site.content_json)}


@app.post("/api/site-content/{site_id}")
async def update_site_content(site_id: str, data: dict = Body(...), session: AsyncSession = Depends(get_session)):
    site = await session.get(Site, site_id)
    if not site: return {"ok": False, "error": "Сайт не найден"}
    site.content_json = json.dumps(data, ensure_ascii=False)
    await session.commit()
    from render import render_site, save_site
    from deploy import deploy_to_github
    try:
        content = SiteContent.model_validate(data)
        html = render_site(content, server_url=os.getenv("SERVER_URL"), site_id=site_id)
        path = save_site(html, filename="index.html")
        await asyncio.to_thread(deploy_to_github, site_id, path)
        return {"ok": True, "message": "Сохранено и опубликовано"}
    except Exception as e:
        logger.error(f"Ошибка при обновлении: {e}")
        return {"ok": False, "error": str(e)}


# ============ УПРАВЛЕНИЕ ПЕРСОНАЛОМ ============
@app.get("/api/masters/{site_id}")
async def get_masters(site_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Master).where(Master.site_id == site_id))
    masters = result.scalars().all()
    return {"masters": [
        {"id": m.id, "name": m.name, "role": m.role, "description": m.description,
         "services": m.services.split(",") if m.services else [], "is_active": m.is_active}
        for m in masters
    ]}


@app.post("/api/masters/{site_id}")
async def create_master(site_id: str, data: dict = Body(...), session: AsyncSession = Depends(get_session)):
    services_str = ",".join(data.get("services", []))
    master = Master(site_id=site_id, name=data.get("name", ""), role=data.get("role", ""),
                    description=data.get("description", ""), services=services_str)
    session.add(master)
    await session.commit()
    return {"ok": True, "id": master.id}


@app.delete("/api/masters/{master_id}")
async def delete_master(master_id: int, session: AsyncSession = Depends(get_session)):
    master = await session.get(Master, master_id)
    if master:
        await session.delete(master)
        await session.commit()
    return {"ok": True}


# ============ АНАЛИТИКА ============
@app.get("/api/analytics/{site_id}")
async def get_analytics(site_id: str, session: AsyncSession = Depends(get_session)):
    total_contacts = await session.scalar(
        select(func.count()).select_from(ClientContact).where(ClientContact.site_id == site_id)) or 0
    verified_contacts = await session.scalar(
        select(func.count()).select_from(ClientContact).where(ClientContact.site_id == site_id,
                                                              ClientContact.is_verified == True)) or 0
    total_bookings = await session.scalar(
        select(func.count()).select_from(Booking).where(Booking.site_id == site_id)) or 0
    total_subscribers = await session.scalar(
        select(func.count()).select_from(Subscriber).where(Subscriber.site_id == site_id)) or 0
    return {"site_id": site_id, "total_contacts": total_contacts, "verified_contacts": verified_contacts,
            "unverified_contacts": total_contacts - verified_contacts, "total_bookings": total_bookings,
            "total_subscribers": total_subscribers}


@app.get("/api/bookings/{site_id}")
async def get_bookings(site_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Booking).where(Booking.site_id == site_id).order_by(Booking.date.desc(), Booking.time.desc()))
    bookings = result.scalars().all()
    return {"bookings": [
        {"date": b.date, "time": b.time, "master": b.master, "service": b.service, "name": b.name, "phone": b.phone} for
        b in bookings]}


# ============ ПОДПИСЧИКИ ============
@app.post("/api/subscribe")
async def subscribe(sub: SubscriberRequest, session: AsyncSession = Depends(get_session)):
    existing = await session.get(Subscriber, (sub.user_id, sub.site_id))
    if not existing:
        session.add(Subscriber(user_id=sub.user_id, site_id=sub.site_id))
        await session.commit()
    return {"ok": True}


@app.get("/api/health")
async def health(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Subscriber))
    total = len(result.scalars().all())
    return {"status": "ok", "subscribers": total}


# ============ РЕГИСТРАЦИЯ КЛИЕНТОВ ============
@app.post("/api/register-client/{site_id}")
async def register_client(site_id: str, data: ClientRegisterRequest, background_tasks: BackgroundTasks,
                          session: AsyncSession = Depends(get_session)):
    email = data.email.strip().lower()
    if not email or "@" not in email: return {"ok": False, "error": "Неверный email"}
    existing = await session.execute(
        select(ClientContact).where(ClientContact.site_id == site_id, ClientContact.email == email))
    contact = existing.scalar_one_or_none()
    if contact and contact.is_verified: return {"ok": True, "message": "Уже зарегистрирован"}
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    if contact:
        contact.verification_token = token
        contact.token_expires_at = expires_at
    else:
        contact = ClientContact(site_id=site_id, email=email, verification_token=token, token_expires_at=expires_at)
        session.add(contact)
    await session.commit()
    background_tasks.add_task(send_verification_email, site_id, email, token)
    return {"ok": True, "message": "Письмо отправлено"}


@app.get("/api/check-email/{site_id}")
async def check_email(site_id: str, email: str, session: AsyncSession = Depends(get_session)):
    email = email.strip().lower()
    result = await session.execute(
        select(ClientContact).where(ClientContact.site_id == site_id, ClientContact.email == email))
    contact = result.scalar_one_or_none()
    if not contact: return {"exists": False, "is_verified": False}
    return {"exists": True, "is_verified": bool(contact.is_verified)}


@app.get("/api/verify-client")
async def verify_client(token: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ClientContact).where(ClientContact.verification_token == token))
    contact = result.scalar_one_or_none()
    if not contact: return HTMLResponse("<h2>❌ Неверная или устаревшая ссылка</h2>", status_code=400)
    if contact.token_expires_at:
        expires = contact.token_expires_at
        if expires.tzinfo is None: expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc): return HTMLResponse(
            "<h2>❌ Ссылка истекла. Зарегистрируйтесь заново.</h2>", status_code=400)
    contact.is_verified = True
    contact.verification_token = None
    contact.token_expires_at = None
    await session.commit()
    site_url = f"https://danilka007tuk.github.io/{contact.site_id}/index.html?verified=1"
    return HTMLResponse(f"""
        <html><body style="font-family: Inter, Arial; text-align: center; padding: 50px; background: #f5f9ff;">
        <h2 style="color: #1976d2;">✅ Email подтверждён!</h2>
        <p>Сейчас вы будете перенаправлены на сайт.</p>
        <script>
            localStorage.setItem('user_email', '{contact.email}');
            setTimeout(() => {{ window.location.href = '{site_url}'; }}, 2000);
        </script>
        <p><a href="{site_url}">Перейти на сайт</a></p>
        </body></html>
    """)


async def send_verification_email(site_id: str, email: str, token: str):
    resend.api_key = os.getenv("RESEND_API_KEY")
    if not resend.api_key: return
    server_url = os.getenv("SERVER_URL", "https://api.generator-beloretsk.ru")
    verify_url = f"{server_url}/api/verify-client?token={token}"
    html = f"<h2>Здравствуйте!</h2><p>Вы зарегистрировались на сайте <strong>{site_id}</strong>.</p><p>Чтобы подтвердить email, перейдите по ссылке:</p><p><a href='{verify_url}'>Подтвердить email</a></p><p>Или скопируйте: {verify_url}</p><p>Ссылка действует 24 часа.</p>"
    try:
        params = {"from": "noreply@generator-beloretsk.ru", "to": [email], "subject": "Подтверждение email",
                  "html": html}
        response = await asyncio.to_thread(resend.Emails.send, params)
        logger.info(f"Email через Resend отправлен {email}. ID: {response.get('id')}")
    except Exception as e:
        logger.error(f"Ошибка отправки email через Resend: {e}")


# ============ БРОНИРОВАНИЕ ============
@app.get("/api/busy/{site_id}")
async def get_busy(site_id: str, session: AsyncSession = Depends(get_session)):
    today = date_type.today().isoformat()
    result = await session.execute(
        select(Booking.date, Booking.time, Booking.master, Booking.service).where(Booking.site_id == site_id,
                                                                                  Booking.date >= today))
    busy = [{"date": row.date, "time": row.time, "master": row.master, "service": row.service} for row in result.all()]
    return {"busy": busy}


@app.post("/api/book/{site_id}")
async def create_booking(site_id: str, data: BookingRequest, background_tasks: BackgroundTasks,
                         session: AsyncSession = Depends(get_session)):
    if not data.email: return {"ok": False, "error": "Укажите email"}
    contact_result = await session.execute(
        select(ClientContact).where(ClientContact.site_id == site_id, ClientContact.email == data.email.strip().lower(),
                                    ClientContact.is_verified == True))
    contact = contact_result.scalar_one_or_none()
    if not contact: return {"ok": False, "error": "Email не подтверждён. Зарегистрируйтесь на сайте."}
    existing = await session.execute(
        select(Booking).where(Booking.site_id == site_id, Booking.master == data.master, Booking.date == data.date,
                              Booking.time == data.time))
    if existing.scalar_one_or_none(): return {"ok": False, "error": "Это время у мастера уже занято"}

    # СОХРАНЯЕМ EMAIL В БРОНИ
    booking = Booking(site_id=site_id, master=data.master, date=data.date, time=data.time, service=data.service,
                      name=data.name, phone=data.phone, email=data.email, comment=data.comment)
    session.add(booking)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return {"ok": False, "error": "Это время у мастера только что заняли. Выберите другое."}
    text = f"🔔 Новая бронь!\n\n🌐 Сайт: {site_id}\n👤 Мастер: {data.master or '—'}\n📅 {data.date} в {data.time}\n💼 Услуга: {data.service}\n👤 {data.name}\n📞 {data.phone}\n📧 {data.email or '—'}\n💬 {data.comment or '—'}"
    background_tasks.add_task(send_notifications, text, site_id)
    background_tasks.add_task(send_email_resend, site_id, data)
    return {"ok": True}


async def send_notifications(text: str, site_id: str):
    token = os.getenv("MAX_BOT_TOKEN")
    admin_id = os.getenv("MAX_ADMIN_ID")
    url = "https://platform-api2.max.ru/messages"
    async with async_session() as session:
        result = await session.execute(select(Subscriber.user_id).where(Subscriber.site_id == site_id))
        recipients = {row.user_id for row in result.all()}
    if admin_id:
        try:
            recipients.add(int(admin_id))
        except ValueError:
            logger.warning(f"MAX_ADMIN_ID не число: {admin_id}")
    if not recipients: return
    async with httpx.AsyncClient(timeout=15) as client:
        for user_id in recipients:
            try:
                await client.post(url, params={"user_id": user_id}, headers={"Authorization": token},
                                  json={"text": text}, timeout=15)
            except Exception as e:
                logger.error(f"Не удалось отправить {user_id}: {e}")


async def send_email_resend(site_id: str, data: BookingRequest):
    resend.api_key = os.getenv("RESEND_API_KEY")
    if not resend.api_key or not data.email: return
    try:
        params = {
            "from": "noreply@generator-beloretsk.ru",
            "to": [data.email],
            "subject": f"Подтверждение записи на {data.service}",
            "html": f"<h2>Ваша заявка принята!</h2><p><strong>Сайт:</strong> {site_id}</p><p><strong>Мастер:</strong> {data.master or '—'}</p><p><strong>Услуга:</strong> {data.service}</p><p><strong>Дата:</strong> {data.date} в {data.time}</p><p><strong>Имя:</strong> {data.name}</p><p><strong>Телефон:</strong> {data.phone}</p>"
        }
        response = await asyncio.to_thread(resend.Emails.send, params)
        logger.info(f"Email отправлен {data.email}. ID: {response.get('id')}")
    except Exception as e:
        logger.error(f"Ошибка отправки email: {e}")