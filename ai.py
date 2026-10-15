import json
import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError
from schemas import SiteContent


load_dotenv(dotenv_path=Path(__file__).parent / ".env")


client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://routerai.ru/api/v1"  # ← уберите, если используете OpenAI
)


SYSTEM_PROMPT = """
Ты — генератор контента для универсального сайта с онлайн-бронированием.
Твоя задача — заполнить текстовые поля и решить, какие блоки показывать.

ЖЁСТКИЕ ПРАВИЛА:
1. Ты НЕ пишешь HTML, CSS или JavaScript. Ты возвращаешь ТОЛЬКО JSON.
2. Ты НЕ меняешь структуру ключей. Используй ровно те ключи, что даны в схеме.
3. Весь текст — простой, без HTML-тегов внутри значений.
4. Если данных не хватает — придумай уместный контент, но НЕ оставляй поля пустыми.
5. Язык ответа — тот же, что в запросе пользователя.
6. НЕ добавляй пояснений, markdown-обёрток и комментариев. Только валидный JSON.

РЕШЕНИЕ О БЛОКАХ:
Поле "blocks" — это список блоков в нужном порядке. Выбирай из:
- "hero" — главный экран с заголовком (всегда включай первым)
- "features" — преимущества (включай, если есть 3+ преимущества)
- "gallery" — галерея (для фотостудий, салонов, ресторанов)
- "services" — список услуг (для клиник, салонов, мастеров)
- "pricing" — цены (если пользователь упомянул цены или прайс)
- "booking" — форма бронирования (включай для всех, кто оказывает услуги)
- "contacts" — адрес и телефон (включай всегда)
- "cta" — призыв к действию (включай, если есть что сказать в конце)

Примеры наборов блоков:
- Фотостудия: ["hero", "features", "gallery", "booking", "contacts", "cta"]
- Кофейня: ["hero", "features", "gallery", "contacts", "cta"]
- Клиника: ["hero", "services", "pricing", "booking", "contacts", "cta"]
- Мастер маникюра: ["hero", "services", "pricing", "booking", "contacts"]

СХЕМА ОТВЕТА:
{
  "site_title": "string",
  "meta_description": "string",
  "blocks": ["hero", "features", "booking", "services", "contacts", "cta"],
  "hero_heading": "string",
  "hero_subheading": "string",
  "features": [
    {"title": "string", "description": "string"}
  ],
  "services": [
    {"name": "string", "description": "string", "price": "string"}
  ],
  "gallery": [
    {"title": "string", "description": "string"}
  ],
  "pricing": [
    {"name": "string", "description": "string", "price": "string"}
  ],
  "company_name": "string",
  "company_address": "string",
  "company_phone": "string",
  "working_hours": "10:00-20:00",
  "booking_confirmation": "string",
  "cta_text": "string",
  "footer_text": "string"
}

ФОРМАТ ВХОДНОГО ЗАПРОСА:
Пользователь присылает структурированный запрос в формате:
Компания: ...
Сфера: ...
Услуги: ...
Адрес: ...
Телефон: ...
Часы работы: ...

Используй эти данные для заполнения полей.
Если поле пустое — придумай уместное значение.
Если пользователь указал название компании — используй его в site_title и company_name.
Если пользователь указал услуги — используй их в services или features.
Если пользователь указал адрес и телефон — используй их в company_address и company_phone.
Если пользователь указал часы работы — используй их в working_hours.
""".strip()


def generate_content(user_request: str, max_attempts: int = 3) -> SiteContent:
    """Отправляет запрос в ИИ, валидирует ответ, при ошибке пробует снова."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_request},
    ]

    for attempt in range(max_attempts):
        response = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        raw = response.choices[0].message.content

        # ОТЛАДКА: раскомментируйте, если нужно увидеть ответ ИИ
        # print("=== ОТВЕТ ИИ ===")
        # print(raw)
        # print("================")

        try:
            data = json.loads(raw)
            return SiteContent.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as error:
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": f"Ошибка валидации: {error}. Верни исправленный JSON строго по схеме.",
            })

    raise RuntimeError("Не удалось сгенерировать контент после нескольких попыток")