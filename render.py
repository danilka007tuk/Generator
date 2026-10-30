from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

import bleach
from dotenv import load_dotenv
from jinja2 import (
    Environment,
    FileSystemLoader,
    select_autoescape,
)
from openai import OpenAI

from schemas import (
    ALLOWED_SIDEBAR_ANCHORS,
    ALLOWED_TEMPLATES as SCHEMA_ALLOWED_TEMPLATES,
    DEFAULT_COLOR,
    DEFAULT_SIDEBAR_ANCHORS,
    DEFAULT_TEMPLATE,
    SiteContent,
)


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "output"
IMAGES_DIR = OUTPUT_DIR / "images"

load_dotenv(dotenv_path=BASE_DIR / ".env")

logger = logging.getLogger(__name__)


env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(
        enabled_extensions=("html", "xml"),
        default_for_string=True,
        default=False,
    ),
)


ALLOWED_TAGS = [
    "b",
    "strong",
    "i",
    "em",
    "br",
    "a",
]

ALLOWED_ATTRS = {
    "a": [
        "href",
        "title",
        "target",
        "rel",
    ],
}

ALLOWED_PROTOCOLS = [
    "http",
    "https",
    "mailto",
    "tel",
]

ALLOWED_TEMPLATES = list(SCHEMA_ALLOWED_TEMPLATES)

DEFAULT_SIDEBAR_ITEMS = [
    {
        "label": "Услуги",
        "anchor": "#services",
        "icon": "💼",
    },
    {
        "label": "Галерея",
        "anchor": "#gallery",
        "icon": "🖼",
    },
    {
        "label": "Команда",
        "anchor": "#team",
        "icon": "👥",
    },
    {
        "label": "Контакты",
        "anchor": "#contacts",
        "icon": "📞",
    },
]

HEX_COLOR_RE = re.compile(
    r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$"
)


def _get_provod_client() -> OpenAI | None:
    """
    Создаёт OpenAI-клиент для provod.ai только при необходимости.

    Ленивое создание удобнее для:
    - локальной работы без PROVOD_API_KEY;
    - запуска тестов;
    - импорта render.py без немедленного обращения к env.
    """
    api_key = os.getenv("PROVOD_API_KEY", "").strip()

    if not api_key:
        logger.warning(
            "PROVOD_API_KEY не задан. Генерация изображений отключена."
        )
        return None

    return OpenAI(
        api_key=api_key,
        base_url="https://api.provod.ai/v1",
        timeout=120.0,
        max_retries=2,
    )


def _normalize_output_dir(output_dir: str | Path | None) -> Path:
    """
    Возвращает абсолютный каталог вывода.
    """
    if output_dir is None:
        return OUTPUT_DIR.resolve()

    return Path(output_dir).expanduser().resolve()


def _is_external_url(value: str) -> bool:
    """
    Определяет, является ли значение внешним URL.

    Внешние URL не проверяются через локальную файловую систему.
    """
    value = value.strip().lower()

    return value.startswith(
        (
            "http://",
            "https://",
            "data:",
        )
    )


def _safe_relative_asset_path(value: str) -> Path | None:
    """
    Преобразует относительный путь изображения в безопасный Path.

    Запрещаются:
    - абсолютные пути;
    - Windows-диски;
    - ../;
    - обратные слеши;
    - пустые значения.
    """
    if not isinstance(value, str):
        return None

    value = value.strip()

    if not value or _is_external_url(value):
        return None

    value = value.replace("\\", "/").lstrip("/")

    if not value:
        return None

    path = Path(value)

    if path.is_absolute():
        return None

    if any(part == ".." for part in path.parts):
        return None

    return path


def _asset_exists(
    asset_url: str,
    output_dir: str | Path | None = None,
) -> bool:
    """
    Проверяет существование локального изображения.

    Внешние URL считаются существующими и не перегенерируются.
    """
    if not isinstance(asset_url, str):
        return False

    asset_url = asset_url.strip()

    if not asset_url:
        return False

    if _is_external_url(asset_url):
        return True

    relative_path = _safe_relative_asset_path(asset_url)

    if relative_path is None:
        return False

    base_dir = _normalize_output_dir(output_dir)
    filepath = (base_dir / relative_path).resolve()

    try:
        filepath.relative_to(base_dir)
    except ValueError:
        return False

    return filepath.is_file()


def _write_bytes_atomically(
    filepath: Path,
    content: bytes,
) -> None:
    """
    Атомарно записывает файл.

    Временный файл создаётся рядом с целевым файлом, после чего
    заменяет его через os.replace().
    """
    filepath.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = filepath.with_name(
        f".{filepath.name}.{uuid.uuid4().hex}.tmp"
    )

    try:
        temporary_path.write_bytes(content)
        os.replace(temporary_path, filepath)
    finally:
        if temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                logger.warning(
                    "Не удалось удалить временный файл %s",
                    temporary_path,
                )


def _write_text_atomically(
    filepath: Path,
    content: str,
) -> None:
    """
    Атомарно записывает UTF-8-текст.
    """
    _write_bytes_atomically(
        filepath,
        content.encode("utf-8"),
    )


def generate_image_provod(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    output_dir: str | Path | None = None,
) -> str:
    """
    Генерирует изображение через provod.ai.

    Возвращает относительный путь, например:

        images/abc123.png

    Если output_dir передан, файл сохраняется в:

        <output_dir>/images/abc123.png
    """
    if not isinstance(prompt, str):
        return ""

    prompt = prompt.strip()[:300]

    if not prompt:
        return ""

    if width <= 0 or height <= 0:
        logger.warning(
            "Некорректный размер изображения: %sx%s",
            width,
            height,
        )
        return ""

    client = _get_provod_client()

    if client is None:
        return ""

    base_dir = _normalize_output_dir(output_dir)
    images_dir = base_dir / "images"
    images_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        response = client.images.generate(
            model="openai/gpt-image-2",
            prompt=prompt,
            size=f"{width}x{height}",
            n=1,
        )

        if not response.data:
            logger.error(
                "provod.ai вернул пустой список изображений"
            )
            return ""

        image_data = response.data[0]
        b64_data = getattr(image_data, "b64_json", None)

        if not b64_data or not isinstance(b64_data, str):
            logger.error(
                "provod.ai не вернул b64_json"
            )
            return ""

        try:
            image_bytes = base64.b64decode(
                b64_data,
                validate=True,
            )
        except (ValueError, binascii.Error) as exc:
            logger.error(
                "Некорректный base64 от provod.ai: %s",
                exc,
            )
            return ""

        if not image_bytes:
            logger.error(
                "provod.ai вернул пустое изображение"
            )
            return ""

        filename = f"{uuid.uuid4().hex}.png"
        filepath = images_dir / filename

        _write_bytes_atomically(
            filepath,
            image_bytes,
        )

        return f"images/{filename}"

    except Exception:
        logger.exception(
            "Ошибка генерации картинки через provod.ai"
        )
        return ""


def clean(text: Any) -> str:
    """
    Санитизирует пользовательский HTML-фрагмент.

    Разрешаются только ограниченные теги форматирования и ссылки.
    """
    if not isinstance(text, str):
        return ""

    return bleach.clean(
        text.strip(),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )


def clean_plain_text(text: Any) -> str:
    """
    Очищает строковое значение от HTML.

    Используется для значений, которые не должны содержать разметку:
    - URL;
    - названия файлов;
    - цвета;
    - технические идентификаторы.
    """
    if not isinstance(text, str):
        return ""

    return bleach.clean(
        text.strip(),
        tags=[],
        attributes={},
        strip=True,
    )


def clean_color(
    hex_color: Any,
    default: str = DEFAULT_COLOR,
) -> str:
    """
    Проверяет HEX-цвет и возвращает нормализованный lowercase HEX.

    Поддерживаются:
    - #RGB;
    - #RRGGBB.
    """
    if not isinstance(hex_color, str):
        return default

    value = hex_color.strip()

    if not HEX_COLOR_RE.fullmatch(value):
        return default

    if len(value) == 4:
        value = (
            f"#{value[1] * 2}"
            f"{value[2] * 2}"
            f"{value[3] * 2}"
        )

    return value.lower()


def _normalize_anchor(anchor: Any) -> str:
    """
    Оставляет только разрешённые якоря модальных окон.
    """
    if not isinstance(anchor, str):
        return "#services"

    anchor = anchor.strip().lower()

    if not anchor.startswith("#"):
        anchor = f"#{anchor}"

    if anchor not in ALLOWED_SIDEBAR_ANCHORS:
        return "#services"

    return anchor


def get_sidebar_items(
    content: SiteContent,
) -> list[dict[str, str]]:
    """
    Возвращает безопасные элементы бокового меню.

    Если LLM вернула пустой или некорректный список,
    используется DEFAULT_SIDEBAR_ITEMS.
    """
    items = getattr(content, "sidebar_items", None) or []
    result: list[dict[str, str]] = []

    for item in items:
        if hasattr(item, "model_dump"):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = dict(item)
        else:
            continue

        label = clean(data.get("label", ""))
        anchor = _normalize_anchor(data.get("anchor", ""))
        icon = clean_plain_text(data.get("icon", ""))[:10]

        if not label:
            continue

        result.append(
            {
                "label": label,
                "anchor": anchor,
                "icon": icon,
            }
        )

    if not result:
        result = [
            {
                "label": "Услуги",
                "anchor": "#services",
                "icon": "💼",
            },
            {
                "label": "Галерея",
                "anchor": "#gallery",
                "icon": "🖼",
            },
            {
                "label": "Команда",
                "anchor": "#team",
                "icon": "👥",
            },
            {
                "label": "Контакты",
                "anchor": "#contacts",
                "icon": "📞",
            },
        ]

    return result


def _clean_scalar_fields(
    data: dict[str, Any],
    fields: list[str],
) -> None:
    """
    Очищает текстовые поля данных сайта.
    """
    for key in fields:
        if key in data:
            data[key] = clean(data.get(key, ""))


def _clean_feature_items(
    features: list[dict[str, Any]],
) -> None:
    for feature in features:
        if not isinstance(feature, dict):
            continue

        feature["title"] = clean(feature.get("title", ""))
        feature["description"] = clean(
            feature.get("description", "")
        )


def _clean_service_items(
    services: list[dict[str, Any]],
) -> None:
    for service in services:
        if not isinstance(service, dict):
            continue

        service["name"] = clean(service.get("name", ""))
        service["description"] = clean(
            service.get("description", "")
        )
        service["price"] = clean(service.get("price", ""))
        service["procedure"] = clean(
            service.get("procedure", "")
        )

        # URL не должен превращаться в HTML.
        service["image_url"] = clean_plain_text(
            service.get("image_url", "")
        )[:1000]


def _clean_gallery_items(
    gallery: list[dict[str, Any]],
) -> None:
    for item in gallery:
        if not isinstance(item, dict):
            continue

        item["title"] = clean(item.get("title", ""))
        item["description"] = clean(
            item.get("description", "")
        )
        item["image_prompt"] = clean_plain_text(
            item.get("image_prompt", "")
        )[:300]
        item["image_url"] = clean_plain_text(
            item.get("image_url", "")
        )[:1000]


def _clean_pricing_items(
    pricing: list[dict[str, Any]],
) -> None:
    for item in pricing:
        if not isinstance(item, dict):
            continue

        item["name"] = clean(item.get("name", ""))
        item["description"] = clean(
            item.get("description", "")
        )
        item["price"] = clean(item.get("price", ""))


def _clean_testimonials(
    testimonials: list[dict[str, Any]],
) -> None:
    for testimonial in testimonials:
        if not isinstance(testimonial, dict):
            continue

        testimonial["author"] = clean(
            testimonial.get("author", "")
        )
        testimonial["text"] = clean(
            testimonial.get("text", "")
        )


def _clean_faq(
    faq: list[dict[str, Any]],
) -> None:
    for item in faq:
        if not isinstance(item, dict):
            continue

        item["question"] = clean(
            item.get("question", "")
        )
        item["answer"] = clean(
            item.get("answer", "")
        )


def _clean_team(
    team: list[dict[str, Any]],
) -> None:
    for member in team:
        if not isinstance(member, dict):
            continue

        member["name"] = clean(
            member.get("name", "")
        )
        member["role"] = clean(
            member.get("role", "")
        )
        member["description"] = clean(
            member.get("description", "")
        )

        services = member.get("services", [])

        if not isinstance(services, list):
            services = []

        member["services"] = [
            clean_plain_text(service)[:100]
            for service in services
            if isinstance(service, str)
        ]

        member["image_url"] = clean_plain_text(
            member.get("image_url", "")
        )[:1000]


def _get_existing_or_generate_image(
    existing_url: Any,
    prompt: str,
    width: int,
    height: int,
    output_dir: Path,
) -> str:
    """
    Возвращает существующее изображение либо генерирует новое.
    """
    if isinstance(existing_url, str):
        existing_url = existing_url.strip()
    else:
        existing_url = ""

    if existing_url and _asset_exists(
        existing_url,
        output_dir,
    ):
        return existing_url

    return generate_image_provod(
        prompt=prompt,
        width=width,
        height=height,
        output_dir=output_dir,
    )


def render_site(
    content: SiteContent,
    server_url: str = "",
    site_id: str = "",
    output_dir: str | Path | None = None,
) -> str:
    """
    Рендерит основной index.html.

    output_dir добавлен для изоляции параллельных заданий:

        render_site(
            content,
            server_url,
            site_id,
            output_dir="output/jobs/abc123",
        )

    Если output_dir не указан, используется старый общий output/.
    """
    if not isinstance(content, SiteContent):
        content = SiteContent.model_validate(content)

    render_output_dir = _normalize_output_dir(output_dir)
    data = content.model_dump()

    data["server_url"] = (
        server_url.strip()
        if isinstance(server_url, str) and server_url.strip()
        else "http://127.0.0.1:8001"
    )

    data["site_id"] = (
        site_id.strip()
        if isinstance(site_id, str) and site_id.strip()
        else "site"
    )

    _clean_scalar_fields(
        data,
        [
            "site_title",
            "meta_description",
            "hero_heading",
            "hero_subheading",
            "company_name",
            "company_address",
            "company_phone",
            "working_hours",
            "booking_confirmation",
            "cta_text",
            "footer_text",
            "about_short",
            "about_full",
            "sidebar_cta_text",
        ],
    )

    _clean_feature_items(
        data.get("features", [])
    )

    _clean_service_items(
        data.get("services", [])
    )

    _clean_gallery_items(
        data.get("gallery", [])
    )

    _clean_pricing_items(
        data.get("pricing", [])
    )

    _clean_testimonials(
        data.get("testimonials", [])
    )

    _clean_faq(
        data.get("faq", [])
    )

    _clean_team(
        data.get("team", [])
    )

    data["sidebar_items"] = get_sidebar_items(content)

    hero_prompt = clean_plain_text(
        data.get("hero_image_prompt", "")
    )[:300]

    if not hero_prompt:
        company_name = clean_plain_text(
            data.get("company_name", "business")
        )
        hero_prompt = (
            f"Beautiful interior for {company_name}, "
            "photorealistic"
        )

    existing_hero = data.get("hero_image_url", "")

    hero_url = _get_existing_or_generate_image(
        existing_url=existing_hero,
        prompt=hero_prompt,
        width=1024,
        height=1024,
        output_dir=render_output_dir,
    )

    data["hero_image_url"] = hero_url
    content.hero_image_url = hero_url

    gallery_data = data.get("gallery", [])
    gallery_models = getattr(content, "gallery", []) or []

    for index, item in enumerate(gallery_data):
        if not isinstance(item, dict):
            continue

        prompt = (
            clean_plain_text(
                item.get("image_prompt", "")
            )[:300]
            or clean_plain_text(
                item.get("title", "")
            )[:300]
            or "Beauty salon interior"
        )

        image_url = _get_existing_or_generate_image(
            existing_url=item.get("image_url", ""),
            prompt=prompt,
            width=1024,
            height=768,
            output_dir=render_output_dir,
        )

        item["image_url"] = image_url

        if index < len(gallery_models):
            gallery_models[index].image_url = image_url

    data["color_primary"] = clean_color(
        data.get("color_primary", ""),
        "#667eea",
    )

    data["color_secondary"] = clean_color(
        data.get("color_secondary", ""),
        "#764ba2",
    )

    data["color_bg"] = clean_color(
        data.get("color_bg", ""),
        "#f4f6fa",
    )

    data["color_text"] = clean_color(
        data.get("color_text", ""),
        "#2c3e50",
    )

    data["color_accent"] = clean_color(
        data.get("color_accent", ""),
        "#667eea",
    )

    template_name = data.get(
        "template_name",
        DEFAULT_TEMPLATE,
    )

    if template_name not in ALLOWED_TEMPLATES:
        template_name = DEFAULT_TEMPLATE

    template = env.get_template(template_name)

    return template.render(**data)


def render_register_page(
    content: SiteContent,
    server_url: str = "",
    site_id: str = "",
) -> str:
    """
    Рендерит register.html.

    Эта функция не генерирует изображения.
    Она использует уже сохранённый hero_image_url.
    """
    if not isinstance(content, SiteContent):
        content = SiteContent.model_validate(content)

    data = content.model_dump()

    data["server_url"] = (
        server_url.strip()
        if isinstance(server_url, str) and server_url.strip()
        else "http://127.0.0.1:8001"
    )

    data["site_id"] = (
        site_id.strip()
        if isinstance(site_id, str) and site_id.strip()
        else "site"
    )

    _clean_scalar_fields(
        data,
        [
            "site_title",
            "company_name",
            "meta_description",
        ],
    )

    data["color_primary"] = clean_color(
        data.get("color_primary", ""),
        "#667eea",
    )

    data["color_secondary"] = clean_color(
        data.get("color_secondary", ""),
        "#764ba2",
    )

    data["color_bg"] = clean_color(
        data.get("color_bg", ""),
        "#f4f6fa",
    )

    data["color_text"] = clean_color(
        data.get("color_text", ""),
        "#2c3e50",
    )

    data["color_accent"] = clean_color(
        data.get("color_accent", ""),
        "#667eea",
    )

    data["favicon_url"] = clean_plain_text(
        content.hero_image_url or ""
    )[:1000]

    template = env.get_template("register.html")

    return template.render(**data)


def save_site(
    html: str,
    filename: str = "index.html",
    output_dir: str | Path | None = None,
) -> Path:
    """
    Атомарно сохраняет HTML-файл.

    Совместимые вызовы:

        save_site(html)
        save_site(html, "register.html")
        save_site(html, "index.html", "output/jobs/abc123")
    """
    if not isinstance(html, str):
        raise TypeError("html должен быть строкой")

    if not isinstance(filename, str):
        raise TypeError("filename должен быть строкой")

    filename = filename.strip()

    if not filename:
        raise ValueError("Имя файла не может быть пустым")

    filename_path = Path(filename)

    if (
        filename_path.is_absolute()
        or filename_path.name != filename
        or filename in {".", ".."}
    ):
        raise ValueError(
            "Разрешено сохранять только файл в корне output_dir"
        )

    out = _normalize_output_dir(output_dir)
    out.mkdir(
        parents=True,
        exist_ok=True,
    )

    filepath = (out / filename).resolve()

    try:
        filepath.relative_to(out)
    except ValueError as exc:
        raise ValueError(
            "Путь файла выходит за пределы output_dir"
        ) from exc

    _write_text_atomically(
        filepath,
        html,
    )

    return filepath