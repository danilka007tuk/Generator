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
    base_url="https://routerai.ru/api/v1"
)


SYSTEM_PROMPT = """
Ты — генератор контента для универсального сайта с онлайн-бронированием.
Твоя задача — заполнить текстовые поля, подобрать цвета и решить, какие блоки показывать.

ЖЁСТКИЕ ПРАВИЛА:
1. Ты НЕ пишешь HTML, CSS или JavaScript. Ты возвращаешь ТОЛЬКО JSON.
2. Ты НЕ меняешь структуру ключей. Используй ровно те ключи, что даны в схеме.
3. Весь текст — простой, без HTML-тегов внутри значений.
4. Если данных не хватает — придумай уместный контент, но НЕ оставляй поля пустыми.
5. Язык ответа — тот же, что в запросе пользователя.
6. НЕ добавляй пояснений, markdown-обёрток и комментариев. Только валидный JSON.
7. Если пользователь указал название, адрес, телефон, услуги, часы — используй ИХ.

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

ЦВЕТОВАЯ СХЕМА:
Пользователь выбирает палитру. Ты подбираешь 5 HEX-цветов под неё.

Палитры:
- "blue" — синий, голубой, белый. Пример: {"color_primary": "#1976d2", "color_secondary": "#42a5f5", "color_bg": "#f5f9ff", "color_text": "#1a237e", "color_accent": "#ff6b6b"}
- "red" — красный, бордовый, белый. Пример: {"color_primary": "#d32f2f", "color_secondary": "#ef5350", "color_bg": "#fff5f5", "color_text": "#4a1010", "color_accent": "#ffd54f"}
- "green" — зелёный, изумрудный, белый. Пример: {"color_primary": "#2e7d32", "color_secondary": "#66bb6a", "color_bg": "#f1f8f3", "color_text": "#1b5e20", "color_accent": "#ffb74d"}
- "pink" — розовый, лиловый, белый. Пример: {"color_primary": "#e91e63", "color_secondary": "#f06292", "color_bg": "#fff5f8", "color_text": "#4a148c", "color_accent": "#ffc107"}
- "gold" — золотой, коричневый, кремовый. Пример: {"color_primary": "#b8860b", "color_secondary": "#daa520", "color_bg": "#fdf8f0", "color_text": "#3e2723", "color_accent": "#5d4037"}
- "black" — чёрный, серый, белый. Пример: {"color_primary": "#212121", "color_secondary": "#424242", "color_bg": "#fafafa", "color_text": "#000000", "color_accent": "#ff5722"}
- "auto" — подбери сам под сферу деятельности.

ПРАВИЛА ЦВЕТОВ:
1. Все 5 цветов — валидные HEX (#RRGGBB).
2. color_primary — основной (кнопки, акценты).
3. color_secondary — вторичный (градиенты).
4. color_bg — фон страницы (светлый или тёмный).
5. color_text — цвет текста (контрастный к фону).
6. color_accent — яркий акцент (для CTA-кнопок).

СХЕМА ОТВЕТА:
{
  "template_name": "template-modern.html",
  "palette_name": "blue",
  "color_primary": "#1976d2",
  "color_secondary": "#42a5f5",
  "color_bg": "#f5f9ff",
  "color_text": "#1a237e",
  "color_accent": "#ff6b6b",
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
Пользователь присылает структурированный запрос:
Компания: ...
Сфера: ...
Услуги: ...
Адрес: ...
Телефон: ...
Часы работы: ...
Шаблон: template-modern.html
Палитра: blue

Используй эти данные для заполнения полей.
НЕ меняй template_name и palette_name — используй то, что прислал пользователь.
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