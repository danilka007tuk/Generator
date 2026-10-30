from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from schemas import SiteContent


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

logger = logging.getLogger(__name__)


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = "https://routerai.ru/api/v1"
OPENAI_MODEL = "openai/gpt-4o-mini"
OPENAI_TIMEOUT = 120.0
OPENAI_MAX_RETRIES = 2
MAX_USER_REQUEST_LENGTH = 20_000
MAX_RESPONSE_HISTORY_LENGTH = 20_000


def _create_client() -> OpenAI:
    """
    Создаёт клиент routerai.ru только при фактическом вызове генерации.

    Благодаря ленивому созданию:
    - импорт ai.py не падает без .env;
    - приложение может запускаться для диагностики;
    - ошибка отсутствующего ключа формулируется понятно.
    """
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY не задан в .env. "
            "Генерация контента невозможна."
        )

    return OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        timeout=OPENAI_TIMEOUT,
        max_retries=OPENAI_MAX_RETRIES,
    )


SYSTEM_PROMPT = """
Ты — генератор контента для универсального сайта с онлайн-бронированием.
Твоя задача — заполнить текстовые поля, подобрать цвета, описать картинки
и решить, какие блоки показывать.

ЖЁСТКИЕ ПРАВИЛА:

1. Ты НЕ пишешь HTML, CSS или JavaScript.
   Ты возвращаешь ТОЛЬКО JSON.

2. Ты НЕ меняешь структуру ключей.
   Используй ключи из приведённой схемы.

3. Весь текст — простой, без HTML-тегов внутри значений.

4. Если данных не хватает — придумай уместный контент,
   но НЕ оставляй обязательные поля пустыми.

5. Язык ответа — тот же, что в запросе пользователя.

6. НЕ добавляй пояснений, markdown-обёрток и комментариев.
   Только валидный JSON.

7. Если пользователь указал название, адрес, телефон, услуги или часы,
   используй именно их.

8. ВСЕГДА заполняй sidebar_items минимум четырьмя пунктами.

9. ВСЕГДА заполняй team минимум тремя мастерами.
   У каждого мастера должен быть свой набор из 2–4 услуг.

10. Поля template_name и palette_name должны соответствовать
    значениям из пользовательского запроса.
    Если значения не указаны, используй:
    template-modern.html и auto.

РЕШЕНИЕ О БЛОКАХ:

Поле blocks — список блоков в нужном порядке.

Допустимые блоки:

- hero — главный экран, всегда первым;
- features — преимущества, если есть 3 и более преимущества;
- about — о компании;
- services — список услуг;
- gallery — галерея;
- pricing — цены;
- testimonials — отзывы;
- team — команда;
- faq — частые вопросы;
- booking — форма бронирования;
- contacts — адрес и телефон, всегда включай;
- cta — призыв к действию.

Примеры:

Фотостудия:
["hero", "features", "gallery", "testimonials",
 "booking", "contacts", "cta"]

Кофейня:
["hero", "about", "features", "gallery",
 "contacts", "cta"]

Клиника:
["hero", "services", "team", "pricing",
 "testimonials", "faq", "booking", "contacts", "cta"]

Мастер маникюра:
["hero", "services", "pricing", "testimonials",
 "booking", "contacts", "cta"]

ЦВЕТОВАЯ СХЕМА:

Пользователь выбирает палитру.
Подбери пять HEX-цветов под указанную палитру.

Палитры:

- blue — синий, голубой, белый.
  Пример:
  {"color_primary": "#1976d2",
   "color_secondary": "#42a5f5",
   "color_bg": "#f5f9ff",
   "color_text": "#1a237e",
   "color_accent": "#ff6b6b"}

- red — красный, бордовый, белый.
  Пример:
  {"color_primary": "#d32f2f",
   "color_secondary": "#ef5350",
   "color_bg": "#fff5f5",
   "color_text": "#4a1010",
   "color_accent": "#ffd54f"}

- green — зелёный, изумрудный, белый.
  Пример:
  {"color_primary": "#2e7d32",
   "color_secondary": "#66bb6a",
   "color_bg": "#f1f8f3",
   "color_text": "#1b5e20",
   "color_accent": "#ffb74d"}

- pink — розовый, лиловый, белый.
  Пример:
  {"color_primary": "#e91e63",
   "color_secondary": "#f06292",
   "color_bg": "#fff5f8",
   "color_text": "#4a148c",
   "color_accent": "#ffc107"}

- gold — золотой, коричневый, кремовый.
  Пример:
  {"color_primary": "#b8860b",
   "color_secondary": "#daa520",
   "color_bg": "#fdf8f0",
   "color_text": "#3e2723",
   "color_accent": "#5d4037"}

- black — чёрный, серый, белый.
  Пример:
  {"color_primary": "#212121",
   "color_secondary": "#424242",
   "color_bg": "#fafafa",
   "color_text": "#000000",
   "color_accent": "#ff5722"}

- auto — подбери цвета самостоятельно под сферу деятельности.

Правила цветов:

1. Все пять цветов должны быть валидными HEX #RRGGBB.
2. color_primary — основной цвет кнопок и акцентов.
3. color_secondary — вторичный цвет и градиенты.
4. color_bg — фон страницы.
5. color_text — контрастный цвет текста.
6. color_accent — яркий цвет для CTA.

САЙДБАР:

ВСЕГДА заполняй sidebar_items минимум четырьмя и максимум шестью пунктами.

Каждый пункт содержит:

- label — текст до 40 символов;
- anchor — только один из:
  "#services", "#gallery", "#team", "#contacts", "#booking";
- icon — короткий эмодзи или символ.

Только эти якоря открывают существующие модальные окна
в шаблонах сайта.

Также заполни sidebar_cta_text.
Например: "Забронировать", "Записаться", "Оставить заявку".

ОПИСАНИЯ ДЛЯ КАРТИНОК:

hero_image_prompt — описание на английском языке,
до 300 символов, для генерации фоновой картинки hero-блока.

Опиши:
- сцену;
- стиль;
- освещение;
- ключевые объекты.

gallery[].image_prompt — описание на английском языке,
до 300 символов для каждой картинки галереи.

О КОМПАНИИ:

about_short — 2–3 предложения, до 300 символов.

about_full — один абзац, до 1000 символов.

ОТЗЫВЫ:

Создай 3–5 отзывов.

Каждый отзыв содержит:

- author — до 80 символов;
- text — до 300 символов;
- rating — целое число от 1 до 5.

ЧАСТЫЕ ВОПРОСЫ:

Создай 3–5 вопросов.

Каждый содержит:

- question — до 200 символов;
- answer — до 500 символов.

КОМАНДА:

Создай 3–5 мастеров.

Каждый содержит:

- name — до 80 символов;
- role — до 80 символов;
- description — до 200 символов;
- services — 2–4 услуги этого мастера.

Наборы услуг мастеров должны отличаться.

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
  "blocks": [
    "hero",
    "features",
    "booking",
    "services",
    "contacts",
    "cta"
  ],
  "hero_heading": "string",
  "hero_subheading": "string",
  "hero_image_prompt": "string",
  "hero_image_url": "",
  "about_short": "string",
  "about_full": "string",
  "features": [
    {
      "title": "string",
      "description": "string"
    }
  ],
  "services": [
    {
      "name": "string",
      "description": "string",
      "price": "string",
      "procedure": "",
      "image_url": ""
    }
  ],
  "gallery": [
    {
      "title": "string",
      "description": "string",
      "image_prompt": "string",
      "image_url": ""
    }
  ],
  "pricing": [
    {
      "name": "string",
      "description": "string",
      "price": "string",
      "procedure": "",
      "image_url": ""
    }
  ],
  "testimonials": [
    {
      "author": "string",
      "text": "string",
      "rating": 5
    }
  ],
  "faq": [
    {
      "question": "string",
      "answer": "string"
    }
  ],
  "team": [
    {
      "name": "string",
      "role": "string",
      "description": "string",
      "services": ["string"],
      "image_url": ""
    }
  ],
  "sidebar_enabled": true,
  "sidebar_items": [
    {
      "label": "string",
      "anchor": "#services",
      "icon": "💼"
    }
  ],
  "sidebar_cta_text": "Забронировать",
  "company_name": "string",
  "company_address": "string",
  "company_phone": "string",
  "working_hours": "10:00-20:00",
  "booking_confirmation": "string",
  "cta_text": "string",
  "footer_text": "string"
}

ФОРМАТ ВХОДНОГО ЗАПРОСА:

Компания: ...
Сфера: ...
Услуги: ...
Адрес: ...
Телефон: ...
Часы работы: ...
Шаблон: template-modern.html
Палитра: blue

Используй эти данные для заполнения полей.
Не меняй template_name и palette_name,
если пользователь указал их явно.
""".strip()


def _clean_json_response(raw: Any) -> str:
    """
    Удаляет markdown-обёртки вида:

        ```json
        {...}
        ```

    Также удаляет лишние символы до первой '{'
    и после последней '}'.
    """
    if not isinstance(raw, str):
        return ""

    text = raw.strip()

    if not text:
        return ""

    # Удаляем открывающий fence вместе с необязательным языком.
    text = re.sub(
        r"^\s*```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Удаляем закрывающий fence.
    text = re.sub(
        r"\s*```\s*$",
        "",
        text,
    ).strip()

    first_brace = text.find("{")
    last_brace = text.rfind("}")

    if first_brace >= 0 and last_brace >= first_brace:
        text = text[first_brace:last_brace + 1]

    return text.strip()


def _get_response_content(response: Any) -> str:
    """
    Безопасно извлекает текст ответа OpenAI-compatible API.
    """
    choices = getattr(response, "choices", None)

    if not choices:
        return ""

    first_choice = choices[0]
    message = getattr(first_choice, "message", None)

    if message is None:
        return ""

    content = getattr(message, "content", None)

    if isinstance(content, str):
        return content.strip()

    # Некоторые OpenAI-compatible API могут вернуть content
    # в виде списка частей сообщения.
    if isinstance(content, list):
        parts: list[str] = []

        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue

            if isinstance(part, dict):
                text = part.get("text")

                if isinstance(text, str):
                    parts.append(text)

        return "".join(parts).strip()

    return ""


def _short_error(error: Any, max_length: int = 2000) -> str:
    """
    Ограничивает размер ошибки для логов и повторного prompt.
    """
    text = str(error).strip()

    if len(text) > max_length:
        return f"{text[:max_length]}..."

    return text


def _build_validation_retry_message(
    error: Exception,
) -> str:
    """
    Формирует короткое сообщение для исправления JSON.
    """
    return (
        "Исправь предыдущий ответ. "
        "Верни только один валидный JSON без markdown и комментариев. "
        "Сохрани все необходимые ключи схемы. "
        f"Ошибка валидации: {_short_error(error)}"
    )


def generate_content(
    user_request: str,
    max_attempts: int = 3,
) -> SiteContent:
    """
    Генерирует и валидирует SiteContent.

    Повтор выполняется при:
    - сетевой ошибке;
    - пустом ответе;
    - некорректном JSON;
    - ошибке Pydantic-валидации.

    Важно: функция синхронная. Вызов из async-кода должен выполняться
    через asyncio.to_thread(), чтобы не блокировать event loop.
    """
    if not isinstance(user_request, str):
        raise TypeError(
            "user_request должен быть строкой"
        )

    user_request = user_request.strip()

    if not user_request:
        raise ValueError(
            "Запрос пользователя не может быть пустым"
        )

    if len(user_request) > MAX_USER_REQUEST_LENGTH:
        raise ValueError(
            f"Запрос пользователя слишком длинный. "
            f"Максимум: {MAX_USER_REQUEST_LENGTH} символов."
        )

    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError(
            "max_attempts должен быть целым числом не меньше 1"
        )

    client = _create_client()

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": user_request,
        },
    ]

    last_error: str = "Неизвестная ошибка"

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                response_format={
                    "type": "json_object",
                },
                temperature=0.7,
            )

            raw = _get_response_content(response)

            if not raw:
                raise RuntimeError(
                    "LLM вернула пустой ответ"
                )

        except Exception as error:
            last_error = _short_error(error)

            logger.error(
                "Ошибка запроса к LLM "
                "(попытка %s/%s): %s",
                attempt,
                max_attempts,
                last_error,
            )

            if attempt < max_attempts:
                delay = min(2 ** (attempt - 1), 8)

                logger.info(
                    "Повтор запроса к LLM через %s сек.",
                    delay,
                )

                time.sleep(delay)

            continue

        cleaned = _clean_json_response(raw)

        if not cleaned:
            last_error = "После очистки ответ LLM оказался пустым"

            logger.warning(
                "Попытка %s/%s: пустой JSON-ответ",
                attempt,
                max_attempts,
            )

            if attempt < max_attempts:
                messages.append(
                    {
                        "role": "assistant",
                        "content": raw[:MAX_RESPONSE_HISTORY_LENGTH],
                    }
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Ответ оказался пустым. "
                            "Верни только валидный JSON по схеме."
                        ),
                    }
                )
                time.sleep(min(2 ** (attempt - 1), 8))

            continue

        try:
            parsed_data = json.loads(cleaned)

            if not isinstance(parsed_data, dict):
                raise ValueError(
                    "Корневой JSON должен быть объектом"
                )

            content = SiteContent.model_validate(
                parsed_data,
            )

            logger.info(
                "Контент успешно сгенерирован с попытки %s/%s",
                attempt,
                max_attempts,
            )

            return content

        except json.JSONDecodeError as error:
            last_error = _short_error(error)

            logger.warning(
                "Попытка %s/%s: некорректный JSON: %s",
                attempt,
                max_attempts,
                last_error,
            )

            validation_message = (
                "Ответ не является валидным JSON. "
                "Верни только корректный JSON-объект "
                "без markdown и комментариев."
            )

        except ValidationError as error:
            last_error = _short_error(error)

            logger.warning(
                "Попытка %s/%s: ошибка Pydantic-валидации: %s",
                attempt,
                max_attempts,
                last_error,
            )

            validation_message = _build_validation_retry_message(
                error,
            )

        except ValueError as error:
            last_error = _short_error(error)

            logger.warning(
                "Попытка %s/%s: ошибка структуры JSON: %s",
                attempt,
                max_attempts,
                last_error,
            )

            validation_message = (
                "Корнем ответа должен быть JSON-объект. "
                "Верни только исправленный JSON."
            )

        if attempt < max_attempts:
            messages.append(
                {
                    "role": "assistant",
                    "content": cleaned[
                        :MAX_RESPONSE_HISTORY_LENGTH
                    ],
                }
            )

            messages.append(
                {
                    "role": "user",
                    "content": validation_message,
                }
            )

            delay = min(2 ** (attempt - 1), 8)

            logger.info(
                "Повторная генерация после ошибки через %s сек.",
                delay,
            )

            time.sleep(delay)

    raise RuntimeError(
        "Не удалось сгенерировать контент после "
        f"{max_attempts} попыток. "
        f"Последняя ошибка: {last_error}"
    )