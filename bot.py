import asyncio
import os
import re
import logging
import httpx
from dotenv import load_dotenv

from obabot import create_bot
from obabot.filters import Command
from obabot.types import Message
from obabot.fsm import State, StatesGroup, FSMContext

from ai import generate_content
from render import render_site, save_site
from deploy import deploy_to_github


load_dotenv()

logger = logging.getLogger(__name__)

# Адрес сервера (на Aeza — localhost:8001)
SERVER_URL = os.getenv("SERVER_URL", "http://127.0.0.1:8001")

bot, dp, router = create_bot(max_token=os.getenv("MAX_BOT_TOKEN"))


class Form(StatesGroup):
    """Состояния диалога — 8 шагов."""
    waiting_company_name = State()
    waiting_sphere = State()
    waiting_services = State()
    waiting_address = State()
    waiting_phone = State()
    waiting_hours = State()
    waiting_repo = State()


# ============ ШАГ 0: /start ============

@router.message(Command("start"))
async def start(message: Message, state: FSMContext):
    # Регистрируем пользователя как подписчика
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SERVER_URL}/api/subscribe",
                json={"user_id": message.from_user.id},
                timeout=10,
            )
    except Exception as e:
        print(f"Не удалось зарегистрировать подписчика: {e}")

    await message.answer(
        "👋 Привет! Я помогу создать сайт с онлайн-бронированием.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📋 Как это работает:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Я задам вам **8 вопросов** о вашей компании.\n"
        "Отвечайте на каждый — коротко и по делу.\n"
        "\n"
        "После этого я сгенерирую сайт и опубликую его.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 Шаг 1 из 7. Название компании\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Напишите название вашей компании.\n"
        "\n"
        "Пример: «Фотостудия Кадр»"
    )
    await state.set_state(Form.waiting_company_name)


# ============ ШАГ 1: Название ============

@router.message(Form.waiting_company_name)
async def handle_company_name(message: Message, state: FSMContext):
    company_name = message.text.strip()
    if len(company_name) < 2:
        await message.answer("❌ Слишком короткое название. Попробуйте снова:")
        return

    await state.update_data(company_name=company_name)

    await message.answer(
        f"✅ Отлично, «{company_name}»!\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 Шаг 2 из 7. Сфера деятельности\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Чем занимается ваша компания?\n"
        "\n"
        "Примеры:\n"
        "• Салон красоты\n"
        "• Фотостудия\n"
        "• Кафе\n"
        "• Клиника\n"
        "• Автосервис\n"
        "\n"
        "Напишите одним сообщением:"
    )
    await state.set_state(Form.waiting_sphere)


# ============ ШАГ 2: Сфера ============

@router.message(Form.waiting_sphere)
async def handle_sphere(message: Message, state: FSMContext):
    sphere = message.text.strip()
    if len(sphere) < 2:
        await message.answer("❌ Слишком коротко. Напишите сферу деятельности:")
        return

    await state.update_data(sphere=sphere)

    await message.answer(
        "✅ Принято!\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 Шаг 3 из 7. Услуги\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Перечислите ваши услуги **через запятую**.\n"
        "От 3 до 6 услуг.\n"
        "\n"
        "Пример:\n"
        "«Портретная съёмка, свадебная съёмка, детская съёмка»"
    )
    await state.set_state(Form.waiting_services)


# ============ ШАГ 3: Услуги ============

@router.message(Form.waiting_services)
async def handle_services(message: Message, state: FSMContext):
    services = message.text.strip()
    if len(services) < 5:
        await message.answer("❌ Слишком коротко. Перечислите услуги через запятую:")
        return

    await state.update_data(services=services)

    await message.answer(
        "✅ Услуги записаны!\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 Шаг 4 из 7. Адрес\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Напишите адрес вашей компании.\n"
        "\n"
        "Пример: «Москва, ул. Ленина 10»\n"
        "\n"
        "Если адреса нет — напишите «—»"
    )
    await state.set_state(Form.waiting_address)


# ============ ШАГ 4: Адрес ============

@router.message(Form.waiting_address)
async def handle_address(message: Message, state: FSMContext):
    address = message.text.strip()
    await state.update_data(address=address)

    await message.answer(
        "✅ Адрес записан!\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 Шаг 5 из 7. Телефон\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Напишите номер телефона.\n"
        "\n"
        "Пример: «+7 999 123-45-67»"
    )
    await state.set_state(Form.waiting_phone)


# ============ ШАГ 5: Телефон ============

@router.message(Form.waiting_phone)
async def handle_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if len(phone) < 5:
        await message.answer("❌ Слишком короткий номер. Введите телефон:")
        return

    await state.update_data(phone=phone)

    await message.answer(
        "✅ Телефон записан!\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 Шаг 6 из 7. Часы работы\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Напишите часы работы.\n"
        "\n"
        "Пример: «10:00–20:00»\n"
        "Или: «Ежедневно, кроме понедельника»"
    )
    await state.set_state(Form.waiting_hours)


# ============ ШАГ 6: Часы работы ============

@router.message(Form.waiting_hours)
async def handle_hours(message: Message, state: FSMContext):
    hours = message.text.strip()
    await state.update_data(hours=hours)

    await message.answer(
        "✅ Часы работы записаны!\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 Шаг 7 из 7. Имя для сайта\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Придумайте короткое имя для сайта.\n"
        "\n"
        "Требования:\n"
        "• Только латинские буквы (a-z)\n"
        "• Цифры (0-9)\n"
        "• Дефис (-)\n"
        "• От 3 до 50 символов\n"
        "\n"
        "Примеры:\n"
        "• photo-studio\n"
        "• kofeinya-utro\n"
        "• clinic-moscow"
    )
    await state.set_state(Form.waiting_repo)


# ============ ШАГ 7: Репозиторий + генерация ============

@router.message(Form.waiting_repo)
async def handle_repo(message: Message, state: FSMContext):
    repo_name = message.text.strip().lower()

    if not re.fullmatch(r"[a-z0-9\-]{3,50}", repo_name):
        await message.answer(
            "❌ Имя не подходит.\n"
            "\n"
            "Разрешены только:\n"
            "• латинские буквы a-z\n"
            "• цифры 0-9\n"
            "• дефис -\n"
            "\n"
            "От 3 до 50 символов.\n"
            "\n"
            "Попробуйте снова:"
        )
        return

    # Собираем все данные из FSM
    data = await state.get_data()

    # Формируем структурированный запрос для ИИ
    user_request = (
        f"Компания: {data.get('company_name', '')}\n"
        f"Сфера: {data.get('sphere', '')}\n"
        f"Услуги: {data.get('services', '')}\n"
        f"Адрес: {data.get('address', '')}\n"
        f"Телефон: {data.get('phone', '')}\n"
        f"Часы работы: {data.get('hours', '')}\n"
    )

    await message.answer(
        "⏳ Генерирую контент через ИИ...\n"
        "\n"
        "Это займёт 10–30 секунд."
    )

    try:
        content = await asyncio.to_thread(generate_content, user_request, max_attempts=3)
        html = render_site(content)
        path = save_site(html)
    except Exception as error:
        logger.exception("Ошибка генерации")
        await message.answer(
            "❌ Ошибка генерации.\n"
            "\n"
            f"Причина: {error}\n"
            "\n"
            "Попробуйте ещё раз с командой /start."
        )
        await state.clear()
        return

    await message.answer(
        "⏳ Публикую сайт на GitHub Pages...\n"
        "\n"
        "Это займёт 30–60 секунд."
    )

    try:
        url = await asyncio.to_thread(deploy_to_github, repo_name, path)
    except Exception as error:
        logger.exception("Ошибка деплоя")
        await message.answer(
            "❌ Ошибка публикации.\n"
            "\n"
            f"Причина: {error}\n"
            "\n"
            "Попробуйте ещё раз с командой /start."
        )
        await state.clear()
        return

    await message.answer(
        "✅ Готово! Сайт опубликован.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔗 Ссылка на сайт:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"{url}\n"
        "\n"
        "Откройте ссылку в браузере.\n"
        "Если не открылась сразу — подождите 1–2 минуты.\n"
        "\n"
        "Эту ссылку можно отправлять клиентам."
    )
    await state.clear()


# ============ Уведомления ============

async def notify_admin(text: str):
    """Отправляет уведомление администратору в MAX."""
    admin_id = os.getenv("MAX_ADMIN_ID")
    if not admin_id:
        print("MAX_ADMIN_ID не указан в .env — уведомление не отправлено")
        return
    try:
        await bot.send_message(chat_id=int(admin_id), text=text)
    except Exception as e:
        print(f"Не удалось отправить уведомление админу: {e}")


async def run_bot():
    """Запускает бота. Вызывается из main.py или отдельно."""
    print("=" * 50)
    print("✅ Бот успешно запущен!")
    print("🤖 Платформа: MAX")
    print("📡 Ожидаю сообщения...")
    print("=" * 50)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())