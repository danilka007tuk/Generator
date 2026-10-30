from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import secrets
import shutil
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from obabot import create_bot
from obabot.filters import Command
from obabot.types import Message
from obabot.fsm import FSMContext, State, StatesGroup

from ai import generate_content
from database import async_session
from deploy import deploy_to_github
from models import Site
from render import (
    render_register_page,
    render_site,
    save_site,
)


# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
JOBS_DIR = OUTPUT_DIR / "jobs"

load_dotenv(dotenv_path=BASE_DIR / ".env")

logger = logging.getLogger(__name__)

SERVER_URL = os.getenv(
    "SERVER_URL",
    "https://api.generator-beloretsk.ru",
).strip().rstrip("/")

MAX_TOKEN = os.getenv("MAX_BOT_TOKEN", "").strip()

if not MAX_TOKEN:
    raise RuntimeError(
        "MAX_BOT_TOKEN не задан в .env"
    )


MIN_TEXT_LENGTH = 2
MAX_COMPANY_NAME_LENGTH = 100
MAX_SPHERE_LENGTH = 200
MAX_SERVICES_LENGTH = 1000
MAX_ADDRESS_LENGTH = 200
MAX_PHONE_LENGTH = 50
MAX_HOURS_LENGTH = 100
MAX_EMAIL_LENGTH = 320
MAX_REPO_LENGTH = 50

EMAIL_RE = re.compile(
    r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    re.IGNORECASE,
)

REPO_RE = re.compile(
    r"^[a-z0-9-]{3,50}$",
)


SUPPORTED_TEMPLATES = {
    "template-modern.html",
    "template-light.html",
    "template-warm.html",
    "template-bold.html",
    "template-strict.html",
}

SUPPORTED_PALETTES = {
    "blue",
    "red",
    "green",
    "pink",
    "gold",
    "black",
    "auto",
}


bot, dp, router = create_bot(
    max_token=MAX_TOKEN,
)


# Блокировка действует внутри одного процесса бота.
# Она предотвращает параллельную генерацию одного repo_name.
# Для нескольких процессов дополнительно нужна блокировка
# на уровне БД или внешнего job-сервиса.
_repo_locks: dict[str, asyncio.Lock] = {}
_repo_locks_guard = asyncio.Lock()


class Form(StatesGroup):
    waiting_company_name = State()
    waiting_sphere = State()
    waiting_services = State()
    waiting_address = State()
    waiting_phone = State()
    waiting_hours = State()
    waiting_template = State()
    waiting_palette = State()
    waiting_admin_email = State()
    waiting_repo = State()


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================


def hash_password(password: str) -> str:
    """
    Хеширование совместимо с main.py.

    В текущем проекте используется SHA-256.
    Переход на bcrypt лучше выполнять отдельной миграцией.
    """
    return hashlib.sha256(
        password.encode("utf-8"),
    ).hexdigest()


def get_user_id(message: Message) -> int | None:
    """
    Безопасно получает ID пользователя MAX.
    """
    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)

    if isinstance(user_id, bool):
        return None

    if isinstance(user_id, int) and user_id > 0:
        return user_id

    try:
        parsed = int(user_id)
    except (TypeError, ValueError):
        return None

    return parsed if parsed > 0 else None


def get_message_text(message: Message) -> str:
    """
    Возвращает очищенный текст сообщения.
    """
    value = getattr(message, "text", None)

    if not isinstance(value, str):
        return ""

    return value.strip()


def validate_length(
    value: str,
    minimum: int,
    maximum: int,
) -> bool:
    return minimum <= len(value) <= maximum


def is_valid_email(value: str) -> bool:
    return bool(
        len(value) <= MAX_EMAIL_LENGTH
        and EMAIL_RE.fullmatch(value)
    )


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_repo_name(value: str) -> str:
    return value.strip().lower()


def normalize_template(value: Any) -> str:
    if isinstance(value, str) and value in SUPPORTED_TEMPLATES:
        return value

    return "template-modern.html"


def normalize_palette(value: Any) -> str:
    if isinstance(value, str) and value in SUPPORTED_PALETTES:
        return value

    return "auto"


async def get_repo_lock(repo_name: str) -> asyncio.Lock:
    """
    Возвращает lock для конкретного репозитория.
    """
    async with _repo_locks_guard:
        lock = _repo_locks.get(repo_name)

        if lock is None:
            lock = asyncio.Lock()
            _repo_locks[repo_name] = lock

        return lock


def create_job_directory() -> Path:
    """
    Создаёт уникальный каталог задания.
    """
    JOBS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    job_id = uuid.uuid4().hex
    job_dir = (JOBS_DIR / job_id).resolve()

    job_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    return job_dir


def remove_successful_job_directory(job_dir: Path) -> None:
    """
    Удаляет каталог успешного задания.

    Неудачные задания намеренно сохраняются для диагностики.
    """
    try:
        if job_dir.exists():
            shutil.rmtree(job_dir)
            logger.info(
                "Удалён каталог завершённого задания: %s",
                job_dir,
            )
    except OSError:
        logger.exception(
            "Не удалось удалить каталог задания: %s",
            job_dir,
        )


async def check_site_availability(
    repo_name: str,
    owner_id: int,
) -> bool:
    """
    Проверяет, можно ли использовать имя сайта.

    Возвращает True, если:
    - сайт не существует;
    - либо принадлежит текущему пользователю.
    """
    async with async_session() as db:
        existing = await db.get(
            Site,
            repo_name,
        )

        if not existing:
            return True

        return existing.owner_id == owner_id


async def save_site_to_database(
    repo_name: str,
    owner_id: int,
    content_json: str,
    admin_email: str,
    admin_password_hash: str,
) -> None:
    """
    Создаёт или обновляет запись сайта.
    """
    async with async_session() as db:
        site = await db.get(
            Site,
            repo_name,
        )

        if site is None:
            site = Site(
                id=repo_name,
                content_json=content_json,
                owner_id=owner_id,
                admin_email=admin_email,
                admin_password=admin_password_hash,
            )
            db.add(site)

        else:
            if site.owner_id != owner_id:
                raise PermissionError(
                    "Сайт принадлежит другому пользователю"
                )

            site.content_json = content_json
            site.owner_id = owner_id
            site.admin_email = admin_email
            site.admin_password = admin_password_hash

        await db.commit()


async def subscribe_creator(
    user_id: int,
    repo_name: str,
) -> None:
    """
    Подписывает создателя на уведомления о заявках.
    """
    url = f"{SERVER_URL}/api/subscribe"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
        ) as client:
            response = await client.post(
                url,
                json={
                    "user_id": user_id,
                    "site_id": repo_name,
                },
            )

            if response.status_code >= 400:
                logger.warning(
                    "Не удалось подписать создателя: HTTP %s: %s",
                    response.status_code,
                    response.text[:500],
                )
                return

        logger.info(
            "Создатель %s подписан на сайт %s",
            user_id,
            repo_name,
        )

    except Exception:
        logger.exception(
            "Ошибка подписки создателя %s на %s",
            user_id,
            repo_name,
        )


def build_user_request(data: dict[str, Any]) -> str:
    """
    Формирует структурированный запрос для LLM.
    """
    return (
        f"Компания: {data.get('company_name', '')}\n"
        f"Сфера: {data.get('sphere', '')}\n"
        f"Услуги: {data.get('services', '')}\n"
        f"Адрес: {data.get('address', '')}\n"
        f"Телефон: {data.get('phone', '')}\n"
        f"Часы работы: {data.get('hours', '')}\n"
        f"Шаблон: {data.get('template_name', 'template-modern.html')}\n"
        f"Палитра: {data.get('palette_name', 'auto')}\n"
    )


async def answer_text_required(
    message: Message,
) -> bool:
    """
    Проверяет, что сообщение содержит текст.
    """
    if get_message_text(message):
        return True

    await message.answer(
        "Пожалуйста, отправьте текстовое сообщение.",
    )
    return False


# ============================================================================
# КОМАНДЫ
# ============================================================================


@router.message(Command("start"))
async def start(
    message: Message,
    state: FSMContext,
):
    await state.clear()

    await message.answer(
        "👋 Привет! Я помогу создать сайт "
        "с онлайн-бронированием.\n\n"
        "Я задам несколько вопросов о компании. "
        "После этого ИИ подготовит сайт и опубликует его "
        "на GitHub Pages.\n\n"
        "Для отмены в любой момент отправьте /cancel.\n\n"
        "📝 Шаг 1. Название компании\n\n"
        "Напишите название вашей компании.\n"
        "Пример: «Фотостудия Кадр»",
    )

    await state.set_state(
        Form.waiting_company_name,
    )


@router.message(Command("cancel"))
async def cancel(
    message: Message,
    state: FSMContext,
):
    await state.clear()

    await message.answer(
        "❌ Создание сайта отменено.\n"
        "Чтобы начать заново, отправьте /start.",
    )


# ============================================================================
# FSM — ДАННЫЕ КОМПАНИИ
# ============================================================================


@router.message(Form.waiting_company_name)
async def handle_company_name(
    message: Message,
    state: FSMContext,
):
    if not await answer_text_required(message):
        return

    company_name = get_message_text(message)

    if not validate_length(
        company_name,
        MIN_TEXT_LENGTH,
        MAX_COMPANY_NAME_LENGTH,
    ):
        await message.answer(
            "❌ Название должно содержать от 2 до "
            f"{MAX_COMPANY_NAME_LENGTH} символов. "
            "Попробуйте снова:",
        )
        return

    await state.update_data(
        company_name=company_name,
    )

    await message.answer(
        "✅ Название записано.\n\n"
        "📝 Шаг 2. Сфера деятельности\n\n"
        "Напишите, чем занимается компания.\n"
        "Например: салон красоты, фотостудия, кафе, клиника.",
    )

    await state.set_state(
        Form.waiting_sphere,
    )


@router.message(Form.waiting_sphere)
async def handle_sphere(
    message: Message,
    state: FSMContext,
):
    if not await answer_text_required(message):
        return

    sphere = get_message_text(message)

    if not validate_length(
        sphere,
        MIN_TEXT_LENGTH,
        MAX_SPHERE_LENGTH,
    ):
        await message.answer(
            "❌ Сфера деятельности слишком короткая "
            "или длинная. Попробуйте снова:",
        )
        return

    await state.update_data(
        sphere=sphere,
    )

    await message.answer(
        "✅ Сфера записана.\n\n"
        "📝 Шаг 3. Услуги\n\n"
        "Перечислите услуги через запятую.\n"
        "Желательно указать от 3 до 6 услуг.\n\n"
        "Пример: Портретная съёмка, "
        "свадебная съёмка, детская съёмка",
    )

    await state.set_state(
        Form.waiting_services,
    )


@router.message(Form.waiting_services)
async def handle_services(
    message: Message,
    state: FSMContext,
):
    if not await answer_text_required(message):
        return

    services = get_message_text(message)

    if not validate_length(
        services,
        5,
        MAX_SERVICES_LENGTH,
    ):
        await message.answer(
            "❌ Список услуг слишком короткий "
            "или длинный. Перечислите услуги через запятую:",
        )
        return

    await state.update_data(
        services=services,
    )

    await message.answer(
        "✅ Услуги записаны.\n\n"
        "📝 Шаг 4. Адрес\n\n"
        "Напишите адрес компании.\n"
        "Если адреса нет, отправьте символ «—».",
    )

    await state.set_state(
        Form.waiting_address,
    )


@router.message(Form.waiting_address)
async def handle_address(
    message: Message,
    state: FSMContext,
):
    if not await answer_text_required(message):
        return

    address = get_message_text(message)

    if len(address) > MAX_ADDRESS_LENGTH:
        await message.answer(
            f"❌ Адрес не должен быть длиннее "
            f"{MAX_ADDRESS_LENGTH} символов:",
        )
        return

    await state.update_data(
        address=address,
    )

    await message.answer(
        "✅ Адрес записан.\n\n"
        "📝 Шаг 5. Телефон\n\n"
        "Напишите номер телефона.\n"
        "Пример: +7 999 123-45-67",
    )

    await state.set_state(
        Form.waiting_phone,
    )


@router.message(Form.waiting_phone)
async def handle_phone(
    message: Message,
    state: FSMContext,
):
    if not await answer_text_required(message):
        return

    phone = get_message_text(message)

    if not validate_length(
        phone,
        5,
        MAX_PHONE_LENGTH,
    ):
        await message.answer(
            "❌ Некорректная длина номера телефона. "
            "Введите номер ещё раз:",
        )
        return

    await state.update_data(
        phone=phone,
    )

    await message.answer(
        "✅ Телефон записан.\n\n"
        "📝 Шаг 6. Часы работы\n\n"
        "Напишите часы работы.\n"
        "Пример: 10:00-20:00\n"
        "Или: Ежедневно, кроме понедельника",
    )

    await state.set_state(
        Form.waiting_hours,
    )


@router.message(Form.waiting_hours)
async def handle_hours(
    message: Message,
    state: FSMContext,
):
    if not await answer_text_required(message):
        return

    hours = get_message_text(message)

    if not validate_length(
        hours,
        MIN_TEXT_LENGTH,
        MAX_HOURS_LENGTH,
    ):
        await message.answer(
            "❌ Проверьте часы работы и отправьте их ещё раз:",
        )
        return

    await state.update_data(
        hours=hours,
    )

    await message.answer(
        "✅ Часы работы записаны.\n\n"
        "🎨 Шаг 7. Выберите стиль сайта\n\n"
        "Отправьте номер:\n\n"
        "1. 🌑 Тёмный\n"
        "2. ☀️ Светлый\n"
        "3. 🔥 Тёплый\n"
        "4. ⚡ Яркий\n"
        "5. 🎩 Строгий",
    )

    await state.set_state(
        Form.waiting_template,
    )


# ============================================================================
# FSM — СТИЛЬ И ПАЛИТРА
# ============================================================================


@router.message(Form.waiting_template)
async def handle_template(
    message: Message,
    state: FSMContext,
):
    if not await answer_text_required(message):
        return

    choice = get_message_text(message)

    mapping = {
        "1": "template-modern.html",
        "2": "template-light.html",
        "3": "template-warm.html",
        "4": "template-bold.html",
        "5": "template-strict.html",
    }

    template_name = mapping.get(choice)

    if not template_name:
        await message.answer(
            "❌ Введите номер от 1 до 5:\n"
            "1 — Тёмный\n"
            "2 — Светлый\n"
            "3 — Тёплый\n"
            "4 — Яркий\n"
            "5 — Строгий",
        )
        return

    await state.update_data(
        template_name=template_name,
    )

    await message.answer(
        "✅ Стиль выбран.\n\n"
        "🎨 Шаг 8. Выберите палитру\n\n"
        "Отправьте номер:\n\n"
        "1. 🔵 Синяя\n"
        "2. 🔴 Красная\n"
        "3. 🟢 Зелёная\n"
        "4. 🌸 Розовая\n"
        "5. 🟡 Золотая\n"
        "6. ⚫ Чёрно-белая\n"
        "7. 🎯 Авто",
    )

    await state.set_state(
        Form.waiting_palette,
    )


@router.message(Form.waiting_palette)
async def handle_palette(
    message: Message,
    state: FSMContext,
):
    if not await answer_text_required(message):
        return

    choice = get_message_text(message)

    mapping = {
        "1": "blue",
        "2": "red",
        "3": "green",
        "4": "pink",
        "5": "gold",
        "6": "black",
        "7": "auto",
    }

    palette_name = mapping.get(choice)

    if not palette_name:
        await message.answer(
            "❌ Введите номер от 1 до 7:\n"
            "1 — Синяя\n"
            "2 — Красная\n"
            "3 — Зелёная\n"
            "4 — Розовая\n"
            "5 — Золотая\n"
            "6 — Чёрно-белая\n"
            "7 — Авто",
        )
        return

    await state.update_data(
        palette_name=palette_name,
    )

    await message.answer(
        "✅ Палитра выбрана.\n\n"
        "📧 Шаг 9. Email администратора\n\n"
        "Введите email для входа в кабинет администратора.\n"
        "Пароль будет показан после создания сайта.\n\n"
        "Пример: admin@example.com",
    )

    await state.set_state(
        Form.waiting_admin_email,
    )


@router.message(Form.waiting_admin_email)
async def handle_admin_email(
    message: Message,
    state: FSMContext,
):
    if not await answer_text_required(message):
        return

    email = normalize_email(
        get_message_text(message),
    )

    if not is_valid_email(email):
        await message.answer(
            "❌ Неверный email. Попробуйте снова:",
        )
        return

    await state.update_data(
        admin_email=email,
    )

    await message.answer(
        "✅ Email записан.\n\n"
        "📝 Шаг 10. Имя сайта\n\n"
        "Разрешены только латинские буквы, цифры "
        "и дефис.\n"
        "Длина — от 3 до 50 символов.\n\n"
        "Примеры:\n"
        "photo-studio\n"
        "kofeinya-utro\n"
        "clinic-moscow",
    )

    await state.set_state(
        Form.waiting_repo,
    )


# ============================================================================
# СОЗДАНИЕ САЙТА
# ============================================================================


@router.message(Form.waiting_repo)
async def handle_repo(
    message: Message,
    state: FSMContext,
):
    if not await answer_text_required(message):
        return

    user_id = get_user_id(message)

    if user_id is None:
        logger.error(
            "Не удалось определить user_id для создания сайта",
        )
        await message.answer(
            "❌ Не удалось определить ваш ID. "
            "Попробуйте отправить /start ещё раз.",
        )
        await state.clear()
        return

    repo_name = normalize_repo_name(
        get_message_text(message),
    )

    if not REPO_RE.fullmatch(repo_name):
        await message.answer(
            "❌ Имя не подходит.\n\n"
            "Разрешены только латинские буквы a-z, "
            "цифры 0-9 и дефис.\n"
            "Длина — от 3 до 50 символов.\n\n"
            "Попробуйте снова:",
        )
        return

    if not await check_site_availability(
        repo_name,
        user_id,
    ):
        await message.answer(
            "❌ Это имя уже занято другим пользователем.\n\n"
            "Придумайте другое имя сайта:",
        )
        return

    lock = await get_repo_lock(repo_name)

    if lock.locked():
        await message.answer(
            "⏳ Сайт с таким именем уже создаётся. "
            "Дождитесь завершения или выберите другое имя.",
        )
        return

    async with lock:
        await create_site(
            message=message,
            state=state,
            repo_name=repo_name,
            user_id=user_id,
        )


async def create_site(
    message: Message,
    state: FSMContext,
    repo_name: str,
    user_id: int,
) -> None:
    """
    Полный pipeline создания сайта:
    1. генерация контента;
    2. рендер в отдельный job-каталог;
    3. сохранение сайта в БД;
    4. публикация одним Git-коммитом;
    5. подписка создателя.
    """
    data = await state.get_data()

    template_name = normalize_template(
        data.get("template_name"),
    )
    palette_name = normalize_palette(
        data.get("palette_name"),
    )

    user_request = build_user_request(
        {
            **data,
            "template_name": template_name,
            "palette_name": palette_name,
        }
    )

    await message.answer(
        "⏳ Генерирую контент через ИИ.\n\n"
        "Это может занять 10–30 секунд.",
    )

    job_dir: Path | None = None
    site_url: str | None = None

    try:
        # LLM и рендер выполняются не в event loop.
        content = await asyncio.to_thread(
            generate_content,
            user_request,
            3,
        )

        # Значения из FSM имеют приоритет над ответом LLM.
        content.template_name = template_name
        content.palette_name = palette_name

        job_dir = await asyncio.to_thread(
            create_job_directory,
        )

        html = await asyncio.to_thread(
            render_site,
            content,
            SERVER_URL,
            repo_name,
            job_dir,
        )

        index_path = await asyncio.to_thread(
            save_site,
            html,
            "index.html",
            job_dir,
        )

        register_html = await asyncio.to_thread(
            render_register_page,
            content,
            SERVER_URL,
            repo_name,
        )

        await asyncio.to_thread(
            save_site,
            register_html,
            "register.html",
            job_dir,
        )

        admin_password = secrets.token_urlsafe(12)
        admin_password_hash = hash_password(
            admin_password,
        )

        admin_email = normalize_email(
            str(data.get("admin_email", "")),
        )

        if not is_valid_email(admin_email):
            raise ValueError(
                "Некорректный email администратора",
            )

        # Сохраняем данные сайта до деплоя.
        # Если GitHub недоступен, сайт всё равно остаётся
        # зарегистрированным в БД и может быть опубликован повторно.
        await save_site_to_database(
            repo_name=repo_name,
            owner_id=user_id,
            content_json=content.model_dump_json(),
            admin_email=admin_email,
            admin_password_hash=admin_password_hash,
        )

    except Exception:
        logger.exception(
            "Ошибка генерации сайта %s",
            repo_name,
        )

        await message.answer(
            "❌ Не удалось сгенерировать сайт.\n\n"
            "Проверьте настройки API и попробуйте снова "
            "с командой /start.",
        )

        await state.clear()
        return

    await message.answer(
        "⏳ Контент готов. Публикую сайт на GitHub Pages.\n\n"
        "Это может занять до минуты.",
    )

    try:
        site_url = await asyncio.to_thread(
            deploy_to_github,
            repo_name,
            index_path,
        )

    except Exception:
        logger.exception(
            "Ошибка публикации сайта %s",
            repo_name,
        )

        await message.answer(
            "❌ Контент сохранён, но публикация на GitHub "
            "не завершилась.\n\n"
            "Попробуйте повторить публикацию позже "
            "или обратитесь к администратору.",
        )

        # Не удаляем job_dir: он нужен для диагностики
        # и возможного повторного деплоя.
        await state.clear()
        return

    finally:
        if job_dir is not None and site_url:
            await asyncio.to_thread(
                remove_successful_job_directory,
                job_dir,
            )

    await subscribe_creator(
        user_id,
        repo_name,
    )

    await message.answer(
        "✅ Готово! Сайт опубликован.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔗 Ссылка на сайт:\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{site_url}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔑 Данные администратора:\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Email: {admin_email}\n"
        f"Пароль: {admin_password}\n\n"
        "⚠️ Сохраните пароль и удалите это сообщение. "
        "Оно содержит данные для входа.\n\n"
        "📬 Уведомления о новых заявках будут "
        "приходить в этот чат.",
    )

    await state.clear()


# ============================================================================
# ЗАПУСК
# ============================================================================


async def run_bot() -> None:
    logger.info("Бот успешно запущен")
    logger.info("Платформа: MAX")
    logger.info("Ожидание сообщений")

    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv(
            "LOG_LEVEL",
            "INFO",
        ).upper(),
        format=(
            "%(asctime)s [%(levelname)s] "
            "%(name)s: %(message)s"
        ),
    )

    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")