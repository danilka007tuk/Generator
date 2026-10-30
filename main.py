import os
import logging
import asyncio
import json
import hashlib
import uuid
import re
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import date as date_type, datetime, timedelta
from html import escape as html_escape
from typing import Any

import httpx
import resend
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    Request,
    Depends,
    BackgroundTasks,
    Body,
    Cookie,
    Response,
    UploadFile,
    File,
    HTTPException,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from database import init_db, get_session, async_session
from models import (
    Booking,
    Subscriber,
    ClientContact,
    Site,
    Master,
    MasterSchedule,
)
from schemas import SiteContent


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "output"

load_dotenv(dotenv_path=BASE_DIR / ".env")

logger = logging.getLogger("uvicorn.error")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SERVER_URL = os.getenv("SERVER_URL", "").strip()
IS_LOCAL = not SERVER_URL.startswith("https://")

MAX_IMAGE_SIZE = 10 * 1024 * 1024
MAX_BOOKING_COMMENT_LENGTH = 2000
MAX_TEXT_LENGTH = 500
MAX_EMAIL_LENGTH = 320
MAX_SITE_ID_LENGTH = 100

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

EMAIL_RE = re.compile(
    r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    re.IGNORECASE,
)

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class SubscriberRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    site_id: str = Field(..., min_length=1, max_length=MAX_SITE_ID_LENGTH)


class BookingRequest(BaseModel):
    date: str = Field(..., min_length=10, max_length=10)
    time: str = Field(default="10:00", min_length=5, max_length=5)
    master: str = Field(default="", max_length=MAX_TEXT_LENGTH)
    service: str = Field(..., min_length=1, max_length=MAX_TEXT_LENGTH)
    name: str = Field(..., min_length=1, max_length=MAX_TEXT_LENGTH)
    phone: str = Field(..., min_length=3, max_length=50)
    email: str = Field(default="", max_length=MAX_EMAIL_LENGTH)
    comment: str = Field(default="", max_length=MAX_BOOKING_COMMENT_LENGTH)

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("Дата должна быть в формате YYYY-MM-DD") from exc

        if parsed < date_type.today():
            raise ValueError("Нельзя создать запись на прошедшую дату")

        return value

    @field_validator("time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        if not TIME_RE.fullmatch(value):
            raise ValueError("Время должно быть в формате HH:MM")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()

        if value and (
            len(value) > MAX_EMAIL_LENGTH
            or not EMAIL_RE.fullmatch(value)
        ):
            raise ValueError("Неверный email")

        return value

    @field_validator("name", "phone", "service", "master", "comment")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class ClientRegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=MAX_EMAIL_LENGTH)
    password: str = Field(..., min_length=6, max_length=200)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()

        if not EMAIL_RE.fullmatch(value):
            raise ValueError("Неверный email")

        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        value = value.strip()

        if len(value) < 6:
            raise ValueError("Пароль должен быть минимум 6 символов")

        return value


class LoginRequest(BaseModel):
    site_id: str = Field(..., min_length=1, max_length=MAX_SITE_ID_LENGTH)
    email: str = Field(..., min_length=3, max_length=MAX_EMAIL_LENGTH)
    password: str = Field(default="", max_length=200)
    role: str = Field(default="client", max_length=20)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        value = value.strip().lower()

        if value not in {"admin", "master", "client"}:
            raise ValueError("Недопустимая роль")

        return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Generator API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://danilka007tuk.github.io",
        "https://api.generator-beloretsk.ru",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.fullmatch(email))


def parse_site_content(site: Site | None) -> dict[str, Any]:
    if not site or not site.content_json:
        return {}

    try:
        value = json.loads(site.content_json)
    except (TypeError, json.JSONDecodeError):
        logger.exception(
            "Не удалось прочитать content_json сайта %s",
            getattr(site, "id", "unknown"),
        )
        return {}

    return value if isinstance(value, dict) else {}


def cookie_options() -> dict[str, Any]:
    return {
        "httponly": True,
        "samesite": "none" if not IS_LOCAL else "lax",
        "secure": not IS_LOCAL,
    }


def clear_cookie_options() -> dict[str, Any]:
    return {
        "samesite": "none" if not IS_LOCAL else "lax",
        "secure": not IS_LOCAL,
    }


def site_context(site_id: str, site: Site | None) -> dict[str, Any]:
    content = parse_site_content(site)

    return {
        "site_id": site_id,
        "server_url": SERVER_URL,
        "color_primary": content.get("color_primary", "#1976d2"),
        "color_secondary": content.get("color_secondary", "#1565c0"),
        "color_bg": content.get("color_bg", "#f4f6fa"),
        "color_text": content.get("color_text", "#2c3e50"),
        "logo_text": content.get("company_name", site_id),
        "favicon_url": content.get("hero_image_url", ""),
    }


async def get_existing_site(
    site_id: str,
    session: AsyncSession,
) -> Site:
    site = await session.get(Site, site_id)

    if not site:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Сайт не найден",
        )

    return site


async def require_admin(
    site_id: str,
    role: str | None = Cookie(default=None),
    auth_site_id: str | None = Cookie(default=None, alias="site_id"),
) -> str:
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация администратора",
        )

    if not auth_site_id or auth_site_id != site_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к этому сайту",
        )

    return auth_site_id


async def require_admin_from_cookie(
    site_id: str,
    role: str | None = Cookie(default=None),
    auth_site_id: str | None = Cookie(default=None, alias="site_id"),
) -> str:
    return await require_admin(site_id, role, auth_site_id)


async def require_master(
    site_id: str,
    role: str | None = Cookie(default=None),
    auth_site_id: str | None = Cookie(default=None, alias="site_id"),
    email: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
) -> Master:
    if role != "master":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация мастера",
        )

    if not auth_site_id or auth_site_id != site_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к этому сайту",
        )

    normalized_email = normalize_email(email)

    if not normalized_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email мастера отсутствует",
        )

    result = await session.execute(
        select(Master).where(
            Master.site_id == site_id,
            Master.email == normalized_email,
        )
    )
    master = result.scalar_one_or_none()

    if not master:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Мастер не найден",
        )

    return master


async def require_client(
    site_id: str,
    role: str | None = Cookie(default=None),
    auth_site_id: str | None = Cookie(default=None, alias="site_id"),
    email: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
) -> ClientContact:
    if role != "client":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация клиента",
        )

    if not auth_site_id or auth_site_id != site_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к этому сайту",
        )

    normalized_email = normalize_email(email)

    if not normalized_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email клиента отсутствует",
        )

    result = await session.execute(
        select(ClientContact).where(
            ClientContact.site_id == site_id,
            ClientContact.email == normalized_email,
        )
    )
    contact = result.scalar_one_or_none()

    if not contact:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Клиент не найден",
        )

    return contact


async def verify_master_for_site(
    master_id: int,
    site_id: str,
    session: AsyncSession,
) -> Master:
    master = await session.get(Master, master_id)

    if not master:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Мастер не найден",
        )

    if master.site_id != site_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Мастер принадлежит другому сайту",
        )

    return master


def validate_image_signature(
    contents: bytes,
    content_type: str,
) -> bool:
    if content_type == "image/jpeg":
        return contents.startswith(b"\xff\xd8\xff")

    if content_type == "image/png":
        return contents.startswith(b"\x89PNG\r\n\x1a\n")

    if content_type == "image/webp":
        return (
            len(contents) >= 12
            and contents[:4] == b"RIFF"
            and contents[8:12] == b"WEBP"
        )

    return False


@app.get("/")
async def index():
    return {
        "message": "Сервер работает. Бот запущен отдельно.",
    }


# ============================================================================
# АВТОРИЗАЦИЯ
# ============================================================================


@app.post("/api/login")
async def login(
    data: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    email = normalize_email(data.email)
    cookies = cookie_options()

    if data.role == "admin":
        site = await session.get(Site, data.site_id)

        if (
            site
            and normalize_email(site.admin_email) == email
            and site.admin_password
            and hash_password(data.password) == site.admin_password
        ):
            response.set_cookie(
                key="role",
                value="admin",
                **cookies,
            )
            response.set_cookie(
                key="site_id",
                value=data.site_id,
                **cookies,
            )
            response.delete_cookie(
                key="email",
                **clear_cookie_options(),
            )

            return {
                "ok": True,
                "role": "admin",
                "redirect": f"/dashboard/{data.site_id}",
            }

        return {
            "ok": False,
            "error": "Неверный email или пароль администратора",
        }

    if data.role == "master":
        result = await session.execute(
            select(Master).where(
                Master.site_id == data.site_id,
                Master.email == email,
            )
        )
        master = result.scalar_one_or_none()

        if (
            master
            and master.password_hash
            and hash_password(data.password) == master.password_hash
        ):
            response.set_cookie(
                key="role",
                value="master",
                **cookies,
            )
            response.set_cookie(
                key="site_id",
                value=data.site_id,
                **cookies,
            )
            response.set_cookie(
                key="email",
                value=email,
                **cookies,
            )

            return {
                "ok": True,
                "role": "master",
                "redirect": f"/master_dashboard/{data.site_id}",
            }

        return {
            "ok": False,
            "error": "Неверный email или пароль мастера",
        }

    result = await session.execute(
        select(ClientContact).where(
            ClientContact.site_id == data.site_id,
            ClientContact.email == email,
        )
    )
    contact = result.scalar_one_or_none()

    if (
        contact
        and contact.password_hash
        and hash_password(data.password) == contact.password_hash
    ):
        response.set_cookie(
            key="role",
            value="client",
            **cookies,
        )
        response.set_cookie(
            key="email",
            value=email,
            **cookies,
        )
        response.set_cookie(
            key="site_id",
            value=data.site_id,
            **cookies,
        )

        return {
            "ok": True,
            "role": "client",
            "redirect": f"/client_dashboard/{data.site_id}",
        }

    return {
        "ok": False,
        "error": "Неверный email или пароль",
    }


@app.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    site_id: str = "site",
):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "site_id": site_id,
            "server_url": SERVER_URL,
        },
    )


@app.get("/logout")
async def logout(response: Response):
    options = clear_cookie_options()

    for key in ("role", "email", "site_id"):
        response.delete_cookie(key, **options)

    return {
        "ok": True,
        "message": "Вы вышли",
    }


# ============================================================================
# КАБИНЕТЫ
# ============================================================================


@app.get(
    "/client_dashboard/{site_id}",
    response_class=HTMLResponse,
)
async def client_dashboard(
    site_id: str,
    request: Request,
    role: str | None = Cookie(default=None),
    auth_site_id: str | None = Cookie(default=None, alias="site_id"),
    session: AsyncSession = Depends(get_session),
):
    if role != "client" or auth_site_id != site_id:
        return RedirectResponse(
            url=f"/login?site_id={site_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    site = await session.get(Site, site_id)

    if not site:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Сайт не найден",
        )

    return templates.TemplateResponse(
        request=request,
        name="client_dashboard.html",
        context=site_context(site_id, site),
    )


@app.get(
    "/master_dashboard/{site_id}",
    response_class=HTMLResponse,
)
async def master_dashboard(
    site_id: str,
    request: Request,
    role: str | None = Cookie(default=None),
    auth_site_id: str | None = Cookie(default=None, alias="site_id"),
    email: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
):
    if role != "master" or auth_site_id != site_id or not email:
        return RedirectResponse(
            url=f"/login?site_id={site_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    result = await session.execute(
        select(Master).where(
            Master.site_id == site_id,
            Master.email == normalize_email(email),
        )
    )

    if not result.scalar_one_or_none():
        return RedirectResponse(
            url=f"/login?site_id={site_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    site = await session.get(Site, site_id)

    if not site:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Сайт не найден",
        )

    return templates.TemplateResponse(
        request=request,
        name="master_dashboard.html",
        context=site_context(site_id, site),
    )


@app.get(
    "/dashboard/{site_id}",
    response_class=HTMLResponse,
)
async def dashboard(
    site_id: str,
    request: Request,
    role: str | None = Cookie(default=None),
    auth_site_id: str | None = Cookie(default=None, alias="site_id"),
    session: AsyncSession = Depends(get_session),
):
    if role != "admin" or auth_site_id != site_id:
        return RedirectResponse(
            url=f"/login?site_id={site_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    site = await session.get(Site, site_id)

    if not site:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Сайт не найден",
        )

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=site_context(site_id, site),
    )


# ============================================================================
# ЗАГРУЗКА КАРТИНОК
# ============================================================================


@app.post("/api/upload-image/{site_id}")
async def upload_image(
    site_id: str,
    file: UploadFile = File(...),
    _: str = Depends(require_admin_from_cookie),
    session: AsyncSession = Depends(get_session),
):
    await get_existing_site(site_id, session)

    content_type = (file.content_type or "").lower()

    if content_type not in ALLOWED_IMAGE_TYPES:
        return {
            "ok": False,
            "error": "Только JPG, PNG или WebP",
        }

    contents = await file.read()

    if len(contents) > MAX_IMAGE_SIZE:
        return {
            "ok": False,
            "error": "Файл больше 10 МБ",
        }

    if not contents:
        return {
            "ok": False,
            "error": "Файл пустой",
        }

    if not validate_image_signature(contents, content_type):
        return {
            "ok": False,
            "error": "Содержимое файла не соответствует заявленному формату",
        }

    images_dir = OUTPUT_DIR / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    extension = ALLOWED_IMAGE_TYPES[content_type]
    filename = f"{uuid.uuid4().hex}{extension}"
    filepath = images_dir / filename

    try:
        filepath.write_bytes(contents)
    except OSError:
        logger.exception("Не удалось сохранить изображение %s", filepath)
        return {
            "ok": False,
            "error": "Не удалось сохранить изображение",
        }

    return {
        "ok": True,
        "path": f"images/{filename}",
    }


# ============================================================================
# API КЛИЕНТА
# ============================================================================


@app.get("/api/my-bookings")
async def get_my_bookings(
    contact: ClientContact = Depends(require_client),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Booking)
        .where(
            Booking.site_id == contact.site_id,
            Booking.email == contact.email,
        )
        .order_by(
            Booking.date.desc(),
            Booking.time.desc(),
        )
    )

    bookings = result.scalars().all()

    return {
        "ok": True,
        "bookings": [
            {
                "date": booking.date,
                "time": booking.time,
                "master": booking.master,
                "service": booking.service,
                "name": booking.name,
                "phone": booking.phone,
            }
            for booking in bookings
        ],
    }


@app.get("/api/client-profile")
async def get_client_profile(
    contact: ClientContact = Depends(require_client),
):
    return {
        "ok": True,
        "email": contact.email,
        "is_verified": contact.is_verified,
    }


# ============================================================================
# API МАСТЕРА
# ============================================================================


@app.get("/api/master-bookings")
async def get_master_bookings(
    master: Master = Depends(require_master),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Booking)
        .where(
            Booking.site_id == master.site_id,
            Booking.master == master.name,
        )
        .order_by(
            Booking.date.desc(),
            Booking.time.desc(),
        )
    )

    bookings = result.scalars().all()

    return {
        "ok": True,
        "bookings": [
            {
                "date": booking.date,
                "time": booking.time,
                "service": booking.service,
                "name": booking.name,
                "phone": booking.phone,
                "comment": booking.comment,
            }
            for booking in bookings
        ],
    }


@app.get("/api/master-schedule")
async def get_master_schedule(
    master_id: int | None = None,
    role: str | None = Cookie(default=None),
    auth_site_id: str | None = Cookie(default=None, alias="site_id"),
    email: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
):
    if master_id is not None:
        if role == "admin":
            if not auth_site_id:
                return {
                    "ok": False,
                    "error": "Сайт не указан",
                }

            master = await verify_master_for_site(
                master_id,
                auth_site_id,
                session,
            )

        elif role == "master":
            if not auth_site_id or not email:
                return {
                    "ok": False,
                    "error": "Не авторизован",
                }

            master = await verify_master_for_site(
                master_id,
                auth_site_id,
                session,
            )

            if normalize_email(master.email) != normalize_email(email):
                return {
                    "ok": False,
                    "error": "Нет доступа к расписанию этого мастера",
                }

        else:
            return {
                "ok": False,
                "error": "Не авторизован",
            }

    else:
        if role == "master":
            if not auth_site_id or not email:
                return {
                    "ok": False,
                    "error": "Не авторизован",
                }

            result = await session.execute(
                select(Master).where(
                    Master.site_id == auth_site_id,
                    Master.email == normalize_email(email),
                )
            )
            master = result.scalar_one_or_none()

        elif role == "admin":
            return {
                "ok": False,
                "error": "Для администратора необходимо указать master_id",
            }

        else:
            return {
                "ok": False,
                "error": "Не авторизован",
            }

        if not master:
            return {
                "ok": False,
                "error": "Мастер не найден",
            }

    result = await session.execute(
        select(MasterSchedule).where(
            MasterSchedule.master_id == master.id,
        )
    )

    schedules = result.scalars().all()

    return {
        "ok": True,
        "schedule": [
            {
                "weekday": schedule.weekday,
                "start_time": schedule.start_time,
                "end_time": schedule.end_time,
            }
            for schedule in schedules
        ],
    }


# ============================================================================
# CMS — КОНТЕНТ
# ============================================================================


@app.get("/api/site-content/{site_id}")
async def get_site_content(
    site_id: str,
    _: str = Depends(require_admin_from_cookie),
    session: AsyncSession = Depends(get_session),
):
    site = await get_existing_site(site_id, session)

    return {
        "ok": True,
        "content": parse_site_content(site),
    }


@app.post("/api/site-content/{site_id}")
async def update_site_content(
    site_id: str,
    data: dict = Body(...),
    _: str = Depends(require_admin_from_cookie),
    session: AsyncSession = Depends(get_session),
):
    site = await get_existing_site(site_id, session)

    try:
        content = SiteContent.model_validate(data)
    except Exception as exc:
        logger.warning(
            "Невалидный контент сайта %s: %s",
            site_id,
            exc,
        )
        return {
            "ok": False,
            "error": f"Невалидный контент: {exc}",
        }

    from render import render_site, save_site, render_register_page
    from deploy import deploy_to_github

    try:
        html = await asyncio.to_thread(
            render_site,
            content,
            SERVER_URL,
            site_id,
        )

        path = await asyncio.to_thread(
            save_site,
            html,
            "index.html",
        )

        register_html = await asyncio.to_thread(
            render_register_page,
            content,
            SERVER_URL,
            site_id,
        )

        await asyncio.to_thread(
            save_site,
            register_html,
            "register.html",
        )

        site.content_json = content.model_dump_json()
        await session.commit()

        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    deploy_to_github,
                    site_id,
                    path,
                ),
                timeout=90.0,
            )
        except asyncio.TimeoutError as exc:
            logger.error(
                "Таймаут деплоя на GitHub для %s",
                site_id,
            )
            raise RuntimeError(
                "Превышено время ожидания публикации на GitHub "
                "(90 сек). Проверьте GitHub Pages позже."
            ) from exc

        return {
            "ok": True,
            "message": "Сохранено и опубликовано",
        }

    except Exception as exc:
        logger.exception(
            "Ошибка при обновлении контента сайта %s",
            site_id,
        )
        await session.rollback()

        return {
            "ok": False,
            "error": str(exc),
        }


# ============================================================================
# ПЕРСОНАЛ
# ============================================================================


@app.get("/api/masters/{site_id}")
async def get_masters(
    site_id: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Endpoint оставлен публичным, потому что публичные страницы могут
    загружать список мастеров для формы бронирования.

    Пароли и password_hash в ответ не возвращаются.
    """
    site = await session.get(Site, site_id)

    if not site:
        return {
            "ok": False,
            "error": "Сайт не найден",
            "masters": [],
        }

    result = await session.execute(
        select(Master)
        .where(Master.site_id == site_id)
        .order_by(Master.id.asc())
    )

    masters = result.scalars().all()

    return {
        "ok": True,
        "masters": [
            {
                "id": master.id,
                "name": master.name,
                "role": master.role,
                "description": master.description,
                "services": [
                    item.strip()
                    for item in (master.services or "").split(",")
                    if item.strip()
                ],
                "is_active": master.is_active,
                "email": master.email,
            }
            for master in masters
        ],
    }


@app.post("/api/masters/{site_id}")
async def create_master(
    site_id: str,
    data: dict = Body(...),
    _: str = Depends(require_admin_from_cookie),
    session: AsyncSession = Depends(get_session),
):
    await get_existing_site(site_id, session)

    name = str(data.get("name", "")).strip()
    role_name = str(data.get("role", "")).strip()
    description = str(data.get("description", "")).strip()
    email = normalize_email(data.get("email"))
    password = str(data.get("password", "")).strip()

    raw_services = data.get("services", [])

    if isinstance(raw_services, str):
        services = [
            item.strip()
            for item in raw_services.split(",")
            if item.strip()
        ]
    elif isinstance(raw_services, list):
        services = [
            str(item).strip()
            for item in raw_services
            if str(item).strip()
        ]
    else:
        services = []

    if not name:
        return {
            "ok": False,
            "error": "Укажите имя мастера",
        }

    if not email or not is_valid_email(email):
        return {
            "ok": False,
            "error": "Укажите корректный email мастера",
        }

    if len(password) < 6:
        return {
            "ok": False,
            "error": "Пароль мастера должен быть минимум 6 символов",
        }

    master = Master(
        site_id=site_id,
        name=name,
        role=role_name,
        description=description,
        services=",".join(services),
        email=email,
        password_hash=hash_password(password),
    )

    session.add(master)

    try:
        await session.commit()
        await session.refresh(master)
    except IntegrityError:
        await session.rollback()

        return {
            "ok": False,
            "error": "Мастер с таким email уже существует",
        }

    return {
        "ok": True,
        "id": master.id,
    }


@app.delete("/api/masters/{master_id}")
async def delete_master(
    master_id: int,
    _: str = Depends(require_admin_from_cookie),
    auth_site_id: str | None = Cookie(default=None, alias="site_id"),
    session: AsyncSession = Depends(get_session),
):
    if not auth_site_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сайт не указан",
        )

    master = await verify_master_for_site(
        master_id,
        auth_site_id,
        session,
    )

    await session.execute(
        sql_delete(MasterSchedule).where(
            MasterSchedule.master_id == master_id,
        )
    )

    await session.delete(master)
    await session.commit()

    return {
        "ok": True,
    }


@app.post("/api/master-schedule/{master_id}")
async def update_master_schedule(
    master_id: int,
    data: dict = Body(...),
    _: str = Depends(require_admin_from_cookie),
    auth_site_id: str | None = Cookie(default=None, alias="site_id"),
    session: AsyncSession = Depends(get_session),
):
    if not auth_site_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сайт не указан",
        )

    await verify_master_for_site(
        master_id,
        auth_site_id,
        session,
    )

    raw_schedule = data.get("schedule", [])

    if not isinstance(raw_schedule, list):
        return {
            "ok": False,
            "error": "Поле schedule должно быть массивом",
        }

    normalized_schedule: list[dict[str, Any]] = []
    weekdays: set[int] = set()

    for day in raw_schedule:
        if not isinstance(day, dict):
            return {
                "ok": False,
                "error": "Некорректный день расписания",
            }

        try:
            weekday = int(day["weekday"])
        except (KeyError, TypeError, ValueError):
            return {
                "ok": False,
                "error": "Некорректный weekday",
            }

        start_time = str(day.get("start_time", "")).strip()
        end_time = str(day.get("end_time", "")).strip()

        if weekday not in range(7):
            return {
                "ok": False,
                "error": "weekday должен быть от 0 до 6",
            }

        if weekday in weekdays:
            return {
                "ok": False,
                "error": "Для одного дня нельзя указать несколько интервалов",
            }

        if not TIME_RE.fullmatch(start_time) or not TIME_RE.fullmatch(end_time):
            return {
                "ok": False,
                "error": "Время должно быть в формате HH:MM",
            }

        if start_time >= end_time:
            return {
                "ok": False,
                "error": "Время окончания должно быть позже времени начала",
            }

        weekdays.add(weekday)

        normalized_schedule.append(
            {
                "weekday": weekday,
                "start_time": start_time,
                "end_time": end_time,
            }
        )

    await session.execute(
        sql_delete(MasterSchedule).where(
            MasterSchedule.master_id == master_id,
        )
    )

    for item in normalized_schedule:
        session.add(
            MasterSchedule(
                master_id=master_id,
                weekday=item["weekday"],
                start_time=item["start_time"],
                end_time=item["end_time"],
            )
        )

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()

        return {
            "ok": False,
            "error": "Не удалось сохранить расписание",
        }

    return {
        "ok": True,
    }


# ============================================================================
# АНАЛИТИКА И БРОНИ
# ============================================================================


@app.get("/api/analytics/{site_id}")
async def get_analytics(
    site_id: str,
    _: str = Depends(require_admin_from_cookie),
    session: AsyncSession = Depends(get_session),
):
    await get_existing_site(site_id, session)

    total_contacts = (
        await session.scalar(
            select(func.count())
            .select_from(ClientContact)
            .where(ClientContact.site_id == site_id)
        )
        or 0
    )

    verified_contacts = (
        await session.scalar(
            select(func.count())
            .select_from(ClientContact)
            .where(
                ClientContact.site_id == site_id,
                ClientContact.is_verified.is_(True),
            )
        )
        or 0
    )

    total_bookings = (
        await session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(Booking.site_id == site_id)
        )
        or 0
    )

    total_subscribers = (
        await session.scalar(
            select(func.count())
            .select_from(Subscriber)
            .where(Subscriber.site_id == site_id)
        )
        or 0
    )

    return {
        "site_id": site_id,
        "total_contacts": total_contacts,
        "verified_contacts": verified_contacts,
        "unverified_contacts": total_contacts - verified_contacts,
        "total_bookings": total_bookings,
        "total_subscribers": total_subscribers,
    }


@app.get("/api/bookings/{site_id}")
async def get_bookings(
    site_id: str,
    _: str = Depends(require_admin_from_cookie),
    session: AsyncSession = Depends(get_session),
):
    await get_existing_site(site_id, session)

    result = await session.execute(
        select(Booking)
        .where(Booking.site_id == site_id)
        .order_by(
            Booking.date.desc(),
            Booking.time.desc(),
        )
    )

    bookings = result.scalars().all()

    return {
        "ok": True,
        "bookings": [
            {
                "id": booking.id,
                "date": booking.date,
                "time": booking.time,
                "master": booking.master,
                "service": booking.service,
                "name": booking.name,
                "phone": booking.phone,
                "email": booking.email,
                "comment": booking.comment,
            }
            for booking in bookings
        ],
    }


# ============================================================================
# ПОДПИСЧИКИ
# ============================================================================


@app.post("/api/subscribe")
async def subscribe(
    sub: SubscriberRequest,
    session: AsyncSession = Depends(get_session),
):
    await get_existing_site(sub.site_id, session)

    existing = await session.get(
        Subscriber,
        (sub.user_id, sub.site_id),
    )

    if not existing:
        session.add(
            Subscriber(
                user_id=sub.user_id,
                site_id=sub.site_id,
            )
        )

        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()

    return {
        "ok": True,
    }


@app.get("/api/health")
async def health(
    session: AsyncSession = Depends(get_session),
):
    total = (
        await session.scalar(
            select(func.count()).select_from(Subscriber)
        )
        or 0
    )

    return {
        "status": "ok",
        "subscribers": total,
    }


# ============================================================================
# РЕГИСТРАЦИЯ КЛИЕНТОВ
# ============================================================================


@app.post("/api/register-client/{site_id}")
async def register_client(
    site_id: str,
    data: ClientRegisterRequest,
    session: AsyncSession = Depends(get_session),
):
    await get_existing_site(site_id, session)

    email = normalize_email(data.email)
    password = data.password.strip()

    existing = await session.execute(
        select(ClientContact).where(
            ClientContact.site_id == site_id,
            ClientContact.email == email,
        )
    )

    if existing.scalar_one_or_none():
        return {
            "ok": False,
            "error": "Этот email уже зарегистрирован",
        }

    contact = ClientContact(
        site_id=site_id,
        email=email,
        password_hash=hash_password(password),
        is_verified=True,
    )

    session.add(contact)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()

        return {
            "ok": False,
            "error": "Этот email уже зарегистрирован",
        }

    return {
        "ok": True,
        "message": "Регистрация успешна",
    }


@app.get("/api/check-email/{site_id}")
async def check_email(
    site_id: str,
    email: str,
    session: AsyncSession = Depends(get_session),
):
    await get_existing_site(site_id, session)

    normalized_email = normalize_email(email)

    if not is_valid_email(normalized_email):
        return {
            "exists": False,
            "is_verified": False,
        }

    result = await session.execute(
        select(ClientContact).where(
            ClientContact.site_id == site_id,
            ClientContact.email == normalized_email,
        )
    )

    contact = result.scalar_one_or_none()

    if not contact:
        return {
            "exists": False,
            "is_verified": False,
        }

    return {
        "exists": True,
        "is_verified": bool(contact.is_verified),
    }


# ============================================================================
# БРОНИРОВАНИЕ
# ============================================================================


@app.get("/api/busy/{site_id}")
async def get_busy(
    site_id: str,
    session: AsyncSession = Depends(get_session),
):
    await get_existing_site(site_id, session)

    today = date_type.today().isoformat()

    result = await session.execute(
        select(
            Booking.date,
            Booking.time,
            Booking.master,
            Booking.service,
        )
        .where(
            Booking.site_id == site_id,
            Booking.date >= today,
        )
    )

    return {
        "busy": [
            {
                "date": row.date,
                "time": row.time,
                "master": row.master,
                "service": row.service,
            }
            for row in result.all()
        ],
    }


@app.post("/api/book/{site_id}")
async def create_booking(
    site_id: str,
    data: BookingRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    await get_existing_site(site_id, session)

    email = normalize_email(data.email)

    if not email or not is_valid_email(email):
        return {
            "ok": False,
            "error": "Укажите корректный email",
        }

    contact_result = await session.execute(
        select(ClientContact).where(
            ClientContact.site_id == site_id,
            ClientContact.email == email,
            ClientContact.is_verified.is_(True),
        )
    )

    contact = contact_result.scalar_one_or_none()

    if not contact:
        return {
            "ok": False,
            "error": "Email не подтверждён. Зарегистрируйтесь на сайте.",
        }

    master_name = data.master.strip()
    service_name = data.service.strip()

    if not service_name:
        return {
            "ok": False,
            "error": "Укажите услугу",
        }

    existing = await session.execute(
        select(Booking).where(
            Booking.site_id == site_id,
            Booking.master == master_name,
            Booking.date == data.date,
            Booking.time == data.time,
        )
    )

    if existing.scalar_one_or_none():
        return {
            "ok": False,
            "error": "Это время у мастера уже занято",
        }

    booking = Booking(
        site_id=site_id,
        master=master_name,
        date=data.date,
        time=data.time,
        service=service_name,
        name=data.name.strip(),
        phone=data.phone.strip(),
        email=email,
        comment=data.comment.strip(),
    )

    session.add(booking)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()

        return {
            "ok": False,
            "error": (
                "Это время у мастера только что заняли. "
                "Выберите другое."
            ),
        }

    text = (
        "🔔 Новая бронь!\n\n"
        f"🌐 Сайт: {site_id}\n"
        f"👤 Мастер: {master_name or '—'}\n"
        f"📅 {data.date} в {data.time}\n"
        f"💼 Услуга: {service_name}\n"
        f"👤 {data.name.strip()}\n"
        f"📞 {data.phone.strip()}\n"
        f"📧 {email}\n"
        f"💬 {data.comment.strip() or '—'}"
    )

    background_tasks.add_task(
        send_notifications,
        text,
        site_id,
    )
    background_tasks.add_task(
        send_email_resend,
        site_id,
        data,
    )

    return {
        "ok": True,
    }


# ============================================================================
# УВЕДОМЛЕНИЯ
# ============================================================================


async def send_notifications(
    text: str,
    site_id: str,
):
    token = os.getenv("MAX_BOT_TOKEN")
    admin_id = os.getenv("MAX_ADMIN_ID")
    url = "https://platform-api2.max.ru/messages"

    if not token:
        logger.warning(
            "MAX_BOT_TOKEN не задан, уведомление не отправлено",
        )
        return

    async with async_session() as session:
        result = await session.execute(
            select(Subscriber.user_id).where(
                Subscriber.site_id == site_id,
            )
        )
        recipients = {
            row.user_id
            for row in result.all()
        }

    if admin_id:
        try:
            recipients.add(int(admin_id))
        except ValueError:
            logger.warning(
                "MAX_ADMIN_ID не число: %s",
                admin_id,
            )

    if not recipients:
        return

    headers = {
        "Authorization": token,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        for user_id in recipients:
            try:
                response = await client.post(
                    url,
                    params={"user_id": user_id},
                    headers=headers,
                    json={"text": text},
                )

                if response.status_code >= 400:
                    logger.error(
                        "MAX вернул %s для user_id=%s: %s",
                        response.status_code,
                        user_id,
                        response.text[:500],
                    )

            except Exception:
                logger.exception(
                    "Не удалось отправить MAX-уведомление user_id=%s",
                    user_id,
                )


async def send_email_resend(
    site_id: str,
    data: BookingRequest,
):
    resend.api_key = os.getenv("RESEND_API_KEY")

    if not resend.api_key or not data.email:
        return

    safe_site_id = html_escape(site_id)
    safe_master = html_escape(data.master or "—")
    safe_service = html_escape(data.service)
    safe_date = html_escape(data.date)
    safe_time = html_escape(data.time)
    safe_name = html_escape(data.name)
    safe_phone = html_escape(data.phone)
    safe_comment = html_escape(data.comment or "—")

    params = {
        "from": "noreply@generator-beloretsk.ru",
        "to": [normalize_email(data.email)],
        "subject": f"Подтверждение записи на {data.service}",
        "html": (
            "<h2>Ваша заявка принята!</h2>"
            f"<p><strong>Сайт:</strong> {safe_site_id}</p>"
            f"<p><strong>Мастер:</strong> {safe_master}</p>"
            f"<p><strong>Услуга:</strong> {safe_service}</p>"
            f"<p><strong>Дата:</strong> {safe_date} в {safe_time}</p>"
            f"<p><strong>Имя:</strong> {safe_name}</p>"
            f"<p><strong>Телефон:</strong> {safe_phone}</p>"
            f"<p><strong>Комментарий:</strong> {safe_comment}</p>"
        ),
    }

    try:
        response = await asyncio.to_thread(
            resend.Emails.send,
            params,
        )

        logger.info(
            "Email отправлен %s. ID: %s",
            data.email,
            response.get("id") if isinstance(response, dict) else response,
        )

    except Exception:
        logger.exception(
            "Ошибка отправки email клиенту %s",
            data.email,
        )