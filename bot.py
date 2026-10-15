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

# Адрес сервера (порт 8001)
SERVER_URL = "http://127.0.0.1:8001"

bot, dp, router = create_bot(max_token=os.getenv("MAX_BOT_TOKEN"))


class Form(StatesGroup):
    waiting_topic = State()
    waiting_repo = State()


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
        "Шаг 1. Вы описываете, какой сайт нужен.\n"
        "Шаг 2. Придумываете короткое имя для сайта.\n"
        "Шаг 3. Я генерирую контент и публикую сайт.\n"
        "Шаг 4. Вы получаете ссылку — её можно отправлять клиентам.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 Шаг 1 из 2. Опишите сайт\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Напишите одним сообщением:\n"
        "• Название компании или проекта\n"
        "• Какие услуги вы предлагаете\n"
        "• Адрес\n"
        "• Телефон\n"
        "• Часы работы (если есть)\n"
        "\n"
        "Пример:\n"
        "«Фотостудия Кадр. Услуги: портретная, свадебная, детская съёмка. "
        "Адрес: Москва, ул. Ленина 10. Телефон: +7 999 123-45-67. "
        "Работаем с 10:00 до 20:00.»"
    )
    await state.set_state(Form.waiting_topic)


@router.message(Form.waiting_topic)
async def handle_topic(message: Message, state: FSMContext):
    await state.update_data(topic=message.text)
    await message.answer(
        "✅ Принято!\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 Шаг 2 из 2. Имя для сайта\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Придумайте короткое имя для сайта.\n"
        "\n"
        "Требования:\n"
        "• Только латинские буквы (a-z)\n"
        "• Цифры (0-9)\n"
        "• Дефис (-)\n"
        "• От 3 до 50 символов\n"
        "• Без пробелов и русских букв\n"
        "\n"
        "Примеры:\n"
        "• photo-studio\n"
        "• kofeinya-utro\n"
        "• clinic-moscow\n"
        "\n"
        "Напишите имя одним сообщением:"
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
            "От 3 до 50 символов, без пробелов и русских букв.\n"
            "\n"
            "Попробуйте снова:"
        )
        return

    data = await state.get_data()
    topic = data["topic"]

    await message.answer(
        "⏳ Генерирую контент через ИИ...\n"
        "\n"
        "Это займёт 10–30 секунд."
    )

    try:
        content = await asyncio.to_thread(generate_content, topic, max_attempts=3)
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
        "Откройте ссылку в браузере. Если сайт не открылся сразу — "
        "подождите 1–2 минуты: GitHub Pages собирает страницу не мгновенно.\n"
        "\n"
        "Эту ссылку можно отправлять клиентам."
    )
    await state.clear()


async def run_bot():
    """Запускает бота."""
    print("=" * 50)
    print("✅ Бот успешно запущен!")
    print("🤖 Платформа: MAX")
    print("📡 Ожидаю сообщения...")
    print("=" * 50)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())