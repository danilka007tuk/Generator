# main.py — FastAPI-сервер. Бот запускается отдельно через bot.py
import os
import logging
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
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from database import init_db, get_session, async_session
from models import Booking, Subscriber, ClientContact, Site, Master, MasterSchedule
from schemas import SiteContent

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

logger = logging.getLogger("uvicorn.error")
templates = Jinja2Templates(directory="templates")

SERVER_URL = os.getenv("SERVER_URL", "")
IS_LOCAL = SERVER_URL.startswith("http://127.0.0.1") or SERVER_URL.startswith("http://localhost")


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
    password: str

class LoginRequest(BaseModel):
    site_id: str
    email: str
    password: str = ""
    role: str = "client"

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://danilka007tuk.github.io",
        "https://api.generator-beloretsk.ru",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def index():
    return {"message": "Сервер работает. Бот запущен отдельно."}

# ============ АВТОРИЗАЦИЯ ============
@app.post("/api/login")
async def login(data: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)):
    email = data.email.strip().lower()
    cookie_kwargs = dict(httponly=True, samesite="none" if not IS_LOCAL else "lax", secure=not IS_LOCAL)

    if data.role == "admin":
        site = await session.get(Site, data.site_id)
        if site and site.admin_email == email and site.admin_password:
            if hashlib.sha256(data.password.encode()).hexdigest() == site.admin_password:
                response.set_cookie(key="role", value="admin", **cookie_kwargs)
                response.set_cookie(key="site_id", value=data.site_id, **cookie_kwargs)
                return {"ok": True, "role": "admin", "redirect": f"/dashboard/{data.site_id}"}
        return {"ok": False, "error": "Неверный email или пароль администратора"}

    if data.role == "master":
        result = await session.execute(select(Master).where(Master.site_id == data.site_id, Master.email == email))
        master = result.scalar_one_or_none()
        if master and master.password_hash:
            if hashlib.sha256(data.password.encode()).hexdigest() == master.password_hash:
                response.set_cookie(key="role", value="master", **cookie_kwargs)
                response.set_cookie(key="site_id", value=data.site_id, **cookie_kwargs)
                response.set_cookie(key="email", value=email, **cookie_kwargs)
                return {"ok": True, "role": "master", "redirect": f"/master_dashboard/{data.site_id}"}
        return {"ok": False, "error": "Неверный email или пароль мастера"}

    result = await session.execute(
        select(ClientContact).where(ClientContact.site_id == data.site_id, ClientContact.email == email)
    )
    contact = result.scalar_one_or_none()

    if contact and contact.password_hash:
        if hashlib.sha256(data.password.encode()).hexdigest() == contact.password_hash:
            response.set_cookie(key="role", value="client", **cookie_kwargs)
            response.set_cookie(key="email", value=email, **cookie_kwargs)
            response.set_cookie(key="site_id", value=data.site_id, **cookie_kwargs)
            return {"ok": True, "role": "client", "redirect": f"/client_dashboard/{data.site_id}"}

    return {"ok": False, "error": "Неверный email или пароль"}

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, site_id: str = "site"):
    return templates.TemplateResponse(
        request=request, name="login.html",
        context={"site_id": site_id, "server_url": SERVER_URL}
    )

@app.get("/client_dashboard/{site_id}", response_class=HTMLResponse)
async def client_dashboard(site_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    site = await session.get(Site, site_id)
    content = json.loads(site.content_json) if site else {}
    return templates.TemplateResponse(
        request=request, name="client_dashboard.html",
        context={
            "site_id": site_id, "server_url": SERVER_URL,
            "color_primary": content.get("color_primary", "#1976d2"),
            "color_secondary": content.get("color_secondary", "#1565c0"),
            "color_bg": content.get("color_bg", "#f4f6fa"),
            "color_text": content.get("color_text", "#2c3e50"),
            "logo_text": content.get("company_name", site_id),
            "favicon_url": content.get("hero_image_url", "")
        }
    )

@app.get("/master_dashboard/{site_id}", response_class=HTMLResponse)
async def master_dashboard(site_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    site = await session.get(Site, site_id)
    content = json.loads(site.content_json) if site else {}
    return templates.TemplateResponse(
        request=request, name="master_dashboard.html",
        context={
            "site_id": site_id, "server_url": SERVER_URL,
            "color_primary": content.get("color_primary", "#1976d2"),
            "color_secondary": content.get("color_secondary", "#1565c0"),
            "color_bg": content.get("color_bg", "#f4f6fa"),
            "color_text": content.get("color_text", "#2c3e50"),
            "logo_text": content.get("company_name", site_id),
            "favicon_url": content.get("hero_image_url", "")
        }
    )

@app.get("/dashboard/{site_id}", response_class=HTMLResponse)
async def dashboard(site_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    site = await session.get(Site, site_id)
    content = json.loads(site.content_json) if site else {}
    return templates.TemplateResponse(
        request=request, name="dashboard.html",
        context={
            "site_id": site_id, "server_url": SERVER_URL,
            "color_primary": content.get("color_primary", "#1976d2"),
            "color_secondary": content.get("color_secondary", "#1565c0"),
            "color_bg": content.get("color_bg", "#f4f6fa"),
            "color_text": content.get("color_text", "#2c3e50"),
            "logo_text": content.get("company_name", site_id),
            "favicon_url": content.get("hero_image_url", "")
        }
    )

# ============ API ДЛЯ КАБИНЕТОВ ============
@app.get("/api/my-bookings")
async def get_my_bookings(email: str = Cookie(None), site_id: str = Cookie(None), session: AsyncSession = Depends(get_session)):
    if not email or not site_id: return {"ok": False, "error": "Не авторизован"}
    result = await session.execute(select(Booking).where(Booking.site_id == site_id, Booking.email == email).order_by(Booking.date.desc(), Booking.time.desc()))
    bookings = result.scalars().all()
    return {"ok": True, "bookings": [{"date": b.date, "time": b.time, "master": b.master, "service": b.service, "name": b.name, "phone": b.phone} for b in bookings]}

@app.get("/api/client-profile")
async def get_client_profile(email: str = Cookie(None), site_id: str = Cookie(None), session: AsyncSession = Depends(get_session)):
    if not email or not site_id:
        return {"ok": False, "error": "Не авторизован"}
    result = await session.execute(select(ClientContact).where(ClientContact.site_id == site_id, ClientContact.email == email))
    contact = result.scalar_one_or_none()
    if not contact:
        return {"ok": False, "error": "Клиент не найден"}
    return {"ok": True, "email": contact.email, "is_verified": contact.is_verified}

@app.get("/api/master-bookings")
async def get_master_bookings(email: str = Cookie(None), site_id: str = Cookie(None), session: AsyncSession = Depends(get_session)):
    if not email or not site_id: return {"ok": False, "error": "Не авторизован"}
    email = email.strip().lower()
    master_res = await session.execute(select(Master).where(Master.site_id == site_id, Master.email == email))
    master = master_res.scalar_one_or_none()
    if not master: return {"ok": False, "error": "Мастер не найден"}
    result = await session.execute(select(Booking).where(Booking.site_id == site_id, Booking.master == master.name).order_by(Booking.date.desc(), Booking.time.desc()))
    bookings = result.scalars().all()
    return {"ok": True, "bookings": [{"date": b.date, "time": b.time, "service": b.service, "name": b.name, "phone": b.phone, "comment": b.comment} for b in bookings]}

@app.get("/api/master-schedule")
async def get_master_schedule(email: str = Cookie(None), site_id: str = Cookie(None), master_id: int = None, session: AsyncSession = Depends(get_session)):
    if master_id is not None:
        master = await session.get(Master, master_id)
        if not master: return {"ok": False, "error": "Мастер не найден"}
    else:
        if not email or not site_id: return {"ok": False, "error": "Не авторизован"}
        email = email.strip().lower()
        master_res = await session.execute(select(Master).where(Master.site_id == site_id, Master.email == email))
        master = master_res.scalar_one_or_none()
        if not master: return {"ok": False, "error": "Мастер не найден"}
    result = await session.execute(select(MasterSchedule).where(MasterSchedule.master_id == master.id))
    schedules = result.scalars().all()
    return {"ok": True, "schedule": [{"weekday": s.weekday, "start_time": s.start_time, "end_time": s.end_time} for s in schedules]}

@app.get("/logout")
async def logout(response: Response):
    cookie_kwargs = dict(samesite="none" if not IS_LOCAL else "lax", secure=not IS_LOCAL)
    for key in ("role", "email", "site_id"): response.delete_cookie(key, **cookie_kwargs)
    return {"ok": True, "message": "Вы вышли"}

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
    try:
        content = SiteContent.model_validate(data)
    except Exception as e:
        return {"ok": False, "error": f"Невалидный контент: {e}"}
    site.content_json = json.dumps(data, ensure_ascii=False)
    await session.commit()
    from render import render_site, save_site
    from deploy import deploy_to_github
    try:
        html = render_site(content, server_url=SERVER_URL, site_id=site_id)
        path = save_site(html, filename="index.html")
        await asyncio.to_thread(deploy_to_github, site_id, path)
        return {"ok": True, "message": "Сохранено и опубликовано"}
    except Exception as e:
        logger.error(f"Ошибка при обновлении: {e}")
        return {"ok": False, "error": str(e)}

# ============ ПЕРСОНАЛ ============
@app.get("/api/masters/{site_id}")
async def get_masters(site_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Master).where(Master.site_id == site_id))
    masters = result.scalars().all()
    return {"masters": [{"id": m.id, "name": m.name, "role": m.role, "description": m.description, "services": m.services.split(",") if m.services else [], "is_active": m.is_active, "email": m.email} for m in masters]}

@app.post("/api/masters/{site_id}")
async def create_master(site_id: str, data: dict = Body(...), session: AsyncSession = Depends(get_session)):
    services_str = ",".join(data.get("services", []))
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    password_hash = hashlib.sha256(password.encode()).hexdigest() if password else ""
    master = Master(site_id=site_id, name=data.get("name", ""), role=data.get("role", ""), description=data.get("description", ""), services=services_str, email=email, password_hash=password_hash)
    session.add(master)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return {"ok": False, "error": "Мастер с таким email уже существует"}
    return {"ok": True, "id": master.id}

@app.delete("/api/masters/{master_id}")
async def delete_master(master_id: int, session: AsyncSession = Depends(get_session)):
    master = await session.get(Master, master_id)
    if master:
        await session.delete(master)
        await session.commit()
    return {"ok": True}

@app.post("/api/master-schedule/{master_id}")
async def update_master_schedule(master_id: int, data: dict = Body(...), session: AsyncSession = Depends(get_session)):
    from sqlalchemy import delete as sql_delete
    await session.execute(sql_delete(MasterSchedule).where(MasterSchedule.master_id == master_id))
    for day in data.get("schedule", []):
        sched = MasterSchedule(master_id=master_id, weekday=day["weekday"], start_time=day["start_time"], end_time=day["end_time"])
        session.add(sched)
    await session.commit()
    return {"ok": True}

# ============ АНАЛИТИКА ============
@app.get("/api/analytics/{site_id}")
async def get_analytics(site_id: str, session: AsyncSession = Depends(get_session)):
    total_contacts = await session.scalar(select(func.count()).select_from(ClientContact).where(ClientContact.site_id == site_id)) or 0
    verified_contacts = await session.scalar(select(func.count()).select_from(ClientContact).where(ClientContact.site_id == site_id, ClientContact.is_verified == True)) or 0
    total_bookings = await session.scalar(select(func.count()).select_from(Booking).where(Booking.site_id == site_id)) or 0
    total_subscribers = await session.scalar(select(func.count()).select_from(Subscriber).where(Subscriber.site_id == site_id)) or 0
    return {"site_id": site_id, "total_contacts": total_contacts, "verified_contacts": verified_contacts, "unverified_contacts": total_contacts - verified_contacts, "total_bookings": total_bookings, "total_subscribers": total_subscribers}

@app.get("/api/bookings/{site_id}")
async def get_bookings(site_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Booking).where(Booking.site_id == site_id).order_by(Booking.date.desc(), Booking.time.desc()))
    bookings = result.scalars().all()
    return {"bookings": [{"date": b.date, "time": b.time, "master": b.master, "service": b.service, "name": b.name, "phone": b.phone} for b in bookings]}

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
async def register_client(site_id: str, data: ClientRegisterRequest, session: AsyncSession = Depends(get_session)):
    email = data.email.strip().lower()
    password = data.password.strip()
    if not email or "@" not in email: return {"ok": False, "error": "Неверный email"}
    if len(password) < 6: return {"ok": False, "error": "Пароль должен быть минимум 6 символов"}
    existing = await session.execute(select(ClientContact).where(ClientContact.site_id == site_id, ClientContact.email == email))
    contact = existing.scalar_one_or_none()
    if contact: return {"ok": False, "error": "Этот email уже зарегистрирован"}
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    contact = ClientContact(site_id=site_id, email=email, password_hash=password_hash, is_verified=True)
    session.add(contact)
    await session.commit()
    return {"ok": True, "message": "Регистрация успешна"}

@app.get("/api/check-email/{site_id}")
async def check_email(site_id: str, email: str, session: AsyncSession = Depends(get_session)):
    email = email.strip().lower()
    result = await session.execute(select(ClientContact).where(ClientContact.site_id == site_id, ClientContact.email == email))
    contact = result.scalar_one_or_none()
    if not contact: return {"exists": False, "is_verified": False}
    return {"exists": True, "is_verified": bool(contact.is_verified)}

# ============ БРОНИРОВАНИЕ ============
@app.get("/api/busy/{site_id}")
async def get_busy(site_id: str, session: AsyncSession = Depends(get_session)):
    today = date_type.today().isoformat()
    result = await session.execute(select(Booking.date, Booking.time, Booking.master, Booking.service).where(Booking.site_id == site_id, Booking.date >= today))
    busy = [{"date": row.date, "time": row.time, "master": row.master, "service": row.service} for row in result.all()]
    return {"busy": busy}

@app.post("/api/book/{site_id}")
async def create_booking(site_id: str, data: BookingRequest, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    if not data.email: return {"ok": False, "error": "Укажите email"}
    contact_result = await session.execute(select(ClientContact).where(ClientContact.site_id == site_id, ClientContact.email == data.email.strip().lower(), ClientContact.is_verified == True))
    contact = contact_result.scalar_one_or_none()
    if not contact: return {"ok": False, "error": "Email не подтверждён. Зарегистрируйтесь на сайте."}
    existing = await session.execute(select(Booking).where(Booking.site_id == site_id, Booking.master == data.master, Booking.date == data.date, Booking.time == data.time))
    if existing.scalar_one_or_none(): return {"ok": False, "error": "Это время у мастера уже занято"}
    booking = Booking(site_id=site_id, master=data.master, date=data.date, time=data.time, service=data.service, name=data.name, phone=data.phone, email=data.email, comment=data.comment)
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
        try: recipients.add(int(admin_id))
        except ValueError: logger.warning(f"MAX_ADMIN_ID не число: {admin_id}")
    if not recipients: return
    async with httpx.AsyncClient(timeout=15) as client:
        for user_id in recipients:
            try: await client.post(url, params={"user_id": user_id}, headers={"Authorization": token}, json={"text": text}, timeout=15)
            except Exception as e: logger.error(f"Не удалось отправить {user_id}: {e}")

async def send_email_resend(site_id: str, data: BookingRequest):
    resend.api_key = os.getenv("RESEND_API_KEY")
    if not resend.api_key or not data.email: return
    try:
        params = {"from": "noreply@generator-beloretsk.ru", "to": [data.email], "subject": f"Подтверждение записи на {data.service}", "html": f"<h2>Ваша заявка принята!</h2><p><strong>Сайт:</strong> {site_id}</p><p><strong>Мастер:</strong> {data.master or '—'}</p><p><strong>Услуга:</strong> {data.service}</p><p><strong>Дата:</strong> {data.date} в {data.time}</p><p><strong>Имя:</strong> {data.name}</p><p><strong>Телефон:</strong> {data.phone}</p>"}
        response = await asyncio.to_thread(resend.Emails.send, params)
        logger.info(f"Email отправлен {data.email}. ID: {response.get('id')}")
    except Exception as e:
        logger.error(f"Ошибка отправки email: {e}")