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

SERVER_URL = os.getenv("SERVER_URL", "http://127.0.0.1:8001")

bot, dp, router = create_bot(max_token=os.getenv("MAX_BOT_TOKEN"))


class Form(StatesGroup):
    waiting_company_name = State()
    waiting_sphere = State()
    waiting_services = State()
    waiting_address = State()
    waiting_phone = State()
    waiting_hours = State()
    waiting_template = State()
    waiting_palette = State()
    waiting_repo = State()


# ============ ШАГ 0: /start ============

@router.message(Command("start"))
async def start(message: Message, state: FSMContext):
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
        "Я задам вам несколько вопросов о вашей компании.\n"
        "Отвечайте на каждый — коротко и по делу.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 Шаг 1. Название компании\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Напишите название вашей компании.\n"
        "\n"
        "Пример: «Фотостудия Кадр»"
    )
    await state.set_state(Form.waiting_company_name)


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
        "📝 Шаг 2. Сфера деятельности\n"
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
        "📝 Шаг 3. Услуги\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Перечислите ваши услуги **через запятую**.\n"
        "От 3 до 6 услуг.\n"
        "\n"
        "Пример:\n"
        "«Портретная съёмка, свадебная съёмка, детская съёмка»"
    )
    await state.set_state(Form.waiting_services)


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
        "📝 Шаг 4. Адрес\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Напишите адрес вашей компании.\n"
        "\n"
        "Пример: «Москва, ул. Ленина 10»\n"
        "\n"
        "Если адреса нет — напишите «—»"
    )
    await state.set_state(Form.waiting_address)


@router.message(Form.waiting_address)
async def handle_address(message: Message, state: FSMContext):
    address = message.text.strip()
    await state.update_data(address=address)

    await message.answer(
        "✅ Адрес записан!\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 Шаг 5. Телефон\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Напишите номер телефона.\n"
        "\n"
        "Пример: «+7 999 123-45-67»"
    )
    await state.set_state(Form.waiting_phone)


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
        "📝 Шаг 6. Часы работы\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Напишите часы работы.\n"
        "\n"
        "Пример: «10:00-20:00»\n"
        "Или: «Ежедневно, кроме понедельника»"
    )
    await state.set_state(Form.waiting_hours)


@router.message(Form.waiting_hours)
async def handle_hours(message: Message, state: FSMContext):
    hours = message.text.strip()
    await state.update_data(hours=hours)

    await message.answer(
        "✅ Часы работы записаны!\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎨 Шаг 7. Выберите стиль сайта\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Напишите **номер** стиля:\n"
        "\n"
        "1. 🌑 Тёмный — для IT, стартапов, услуг\n"
        "2. ☀️ Светлый — для салонов, клиник, мастеров\n"
        "3. 🔥 Тёплый — для кафе, ресторанов, отелей\n"
        "4. ⚡ Яркий — для авто, спорта, развлечений"
    )
    await state.set_state(Form.waiting_template)


@router.message(Form.waiting_template)
async def handle_template(message: Message, state: FSMContext):
    choice = message.text.strip()
    mapping = {
        "1": "template-modern.html",
        "2": "template-light.html",
        "3": "template-warm.html",
        "4": "template-bold.html",
    }
    if choice not in mapping:
        await message.answer(
            "❌ Введите номер от 1 до 4:\n"
            "1 — Тёмный\n"
            "2 — Светлый\n"
            "3 — Тёплый\n"
            "4 — Яркий"
        )
        return

    await state.update_data(template_name=mapping[choice])

    await message.answer(
        "✅ Стиль выбран!\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎨 Шаг 8. Выберите палитру\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Напишите **номер** палитры:\n"
        "\n"
        "1. 🔵 Синяя\n"
        "2. 🔴 Красная\n"
        "3. 🟢 Зелёная\n"
        "4. 🌸 Розовая\n"
        "5. 🟡 Золотая\n"
        "6. ⚫ Чёрно-белая\n"
        "7. 🎯 Авто (ИИ подберёт под сферу)"
    )
    await state.set_state(Form.waiting_palette)


@router.message(Form.waiting_palette)
async def handle_palette(message: Message, state: FSMContext):
    choice = message.text.strip()
    mapping = {
        "1": "blue",
        "2": "red",
        "3": "green",
        "4": "pink",
        "5": "gold",
        "6": "black",
        "7": "auto",
    }
    if choice not in mapping:
        await message.answer(
            "❌ Введите номер от 1 до 7:\n"
            "1 — Синяя\n"
            "2 — Красная\n"
            "3 — Зелёная\n"
            "4 — Розовая\n"
            "5 — Золотая\n"
            "6 — Чёрно-белая\n"
            "7 — Авто"
        )
        return

    await state.update_data(palette_name=mapping[choice])

    await message.answer(
        "✅ Палитра выбрана!\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 Шаг 9. Имя для сайта\n"
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
            "Попробуйте снова:"
        )
        return

    data = await state.get_data()

    user_request = (
        f"Компания: {data.get('company_name', '')}\n"
        f"Сфера: {data.get('sphere', '')}\n"
        f"Услуги: {data.get('services', '')}\n"
        f"Адрес: {data.get('address', '')}\n"
        f"Телефон: {data.get('phone', '')}\n"
        f"Часы работы: {data.get('hours', '')}\n"
        f"Шаблон: {data.get('template_name', 'template-modern.html')}\n"
        f"Палитра: {data.get('palette_name', 'auto')}\n"
    )

    await message.answer(
        "⏳ Генерирую контент через ИИ...\n"
        "\n"
        "Это займёт 10–30 секунд."
    )

    try:
        content = await asyncio.to_thread(generate_content, user_request, max_attempts=3)
        # Принудительно сохраняем выбранный шаблон
        content.template_name = data.get('template_name', 'template-modern.html')
        content.palette_name = data.get('palette_name', 'auto')
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
        "Если не открылась сразу — подождите 1–2 минуты."
    )
    await state.clear()


async def run_bot():
    print("=" * 50)
    print("✅ Бот успешно запущен!")
    print("🤖 Платформа: MAX")
    print("📡 Ожидаю сообщения...")
    print("=" * 50)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())