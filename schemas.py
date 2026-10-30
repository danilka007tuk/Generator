from __future__ import annotations

import re
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


ALLOWED_TEMPLATES = {
    "template-modern.html",
    "template-light.html",
    "template-warm.html",
    "template-bold.html",
    "template-strict.html",
}

ALLOWED_SIDEBAR_ANCHORS = {
    "#services",
    "#gallery",
    "#team",
    "#contacts",
    "#booking",
}

DEFAULT_COLOR = "#667eea"
DEFAULT_TEMPLATE = "template-modern.html"
DEFAULT_SIDEBAR_ANCHORS = (
    "#services",
    "#gallery",
    "#team",
    "#contacts",
    "#booking",
)

HEX_COLOR_RE = re.compile(
    r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$"
)


def _is_valid_hex(value: Any) -> bool:
    """
    Проверяет HEX-цвет формата #RGB или #RRGGBB.
    """
    return isinstance(value, str) and bool(
        HEX_COLOR_RE.fullmatch(value.strip())
    )


def _normalize_color(value: Any) -> str:
    """
    Возвращает нормализованный HEX-цвет.

    При некорректном значении применяется безопасный fallback.
    """
    if not _is_valid_hex(value):
        return DEFAULT_COLOR

    value = value.strip()

    if len(value) == 4:
        value = (
            f"#{value[1] * 2}"
            f"{value[2] * 2}"
            f"{value[3] * 2}"
        )

    return value.lower()


def _normalize_anchor(value: Any) -> str:
    """
    Нормализует якорь сайдбара.

    Неизвестные якоря заменяются на #services.
    """
    if not isinstance(value, str):
        return "#services"

    anchor = value.strip().lower()

    if not anchor.startswith("#"):
        anchor = f"#{anchor}"

    if anchor not in ALLOWED_SIDEBAR_ANCHORS:
        return "#services"

    return anchor


class ContentBaseModel(BaseModel):
    """
    Общая конфигурация моделей контента.

    extra='ignore' позволяет безопасно принимать лишние поля,
    которые может вернуть LLM или старый content_json.

    validate_default=True гарантирует применение валидаторов
    к значениям по умолчанию.
    """

    model_config = ConfigDict(
        extra="ignore",
        validate_default=True,
        str_strip_whitespace=True,
    )


class Feature(ContentBaseModel):
    """
    Одно преимущество.
    """

    title: str = Field(
        default="",
        max_length=60,
    )

    description: str = Field(
        default="",
        max_length=300,
    )


class ServiceItem(ContentBaseModel):
    """
    Одна услуга.
    """

    name: str = Field(
        default="",
        max_length=80,
    )

    description: str = Field(
        default="",
        max_length=200,
    )

    price: str = Field(
        default="",
        max_length=50,
    )

    procedure: str = Field(
        default="",
        max_length=500,
    )

    image_url: str = Field(
        default="",
        max_length=1000,
    )


class GalleryItem(ContentBaseModel):
    """
    Один элемент галереи.
    """

    title: str = Field(
        default="",
        max_length=80,
    )

    description: str = Field(
        default="",
        max_length=200,
    )

    image_prompt: str = Field(
        default="",
        max_length=300,
    )

    image_url: str = Field(
        default="",
        max_length=1000,
    )


class Testimonial(ContentBaseModel):
    """
    Один отзыв.
    """

    author: str = Field(
        default="",
        max_length=80,
    )

    text: str = Field(
        default="",
        max_length=300,
    )

    rating: int = Field(
        default=5,
        ge=1,
        le=5,
    )


class FAQItem(ContentBaseModel):
    """
    Один вопрос и ответ.
    """

    question: str = Field(
        default="",
        max_length=200,
    )

    answer: str = Field(
        default="",
        max_length=500,
    )


class SidebarItem(ContentBaseModel):
    """
    Один пункт сайдбара.
    """

    label: str = Field(
        default="",
        max_length=40,
    )

    anchor: str = Field(
        default="#services",
        max_length=40,
    )

    icon: str = Field(
        default="",
        max_length=10,
    )

    @field_validator("anchor", mode="before")
    @classmethod
    def fix_anchor(cls, value: Any) -> str:
        return _normalize_anchor(value)


class TeamMember(ContentBaseModel):
    """
    Один член команды.
    """

    name: str = Field(
        default="",
        max_length=80,
    )

    role: str = Field(
        default="",
        max_length=80,
    )

    description: str = Field(
        default="",
        max_length=200,
    )

    services: list[str] = Field(
        default_factory=list,
        max_length=20,
    )

    image_url: str = Field(
        default="",
        max_length=1000,
    )


class SiteContent(ContentBaseModel):
    """
    Полное содержимое сайта.

    Модель используется для:
    - валидации ответа LLM;
    - загрузки контента из CMS;
    - сохранения content_json;
    - рендера HTML-шаблонов.
    """

    # ========================================================================
    # Шаблон и палитра
    # ========================================================================

    template_name: str = Field(
        default=DEFAULT_TEMPLATE,
        max_length=50,
    )

    palette_name: str = Field(
        default="auto",
        max_length=30,
    )

    # ========================================================================
    # Цвета
    # ========================================================================

    color_primary: str = Field(
        default="#667eea",
        max_length=20,
    )

    color_secondary: str = Field(
        default="#764ba2",
        max_length=20,
    )

    color_bg: str = Field(
        default="#f4f6fa",
        max_length=20,
    )

    color_text: str = Field(
        default="#2c3e50",
        max_length=20,
    )

    color_accent: str = Field(
        default="#667eea",
        max_length=20,
    )

    # ========================================================================
    # Мета
    # ========================================================================

    site_title: str = Field(
        default="",
        max_length=60,
    )

    meta_description: str = Field(
        default="",
        max_length=160,
    )

    # ========================================================================
    # Блоки
    # ========================================================================

    blocks: list[str] = Field(
        default_factory=lambda: [
            "hero",
            "features",
            "booking",
            "services",
            "contacts",
            "cta",
        ],
        max_length=30,
    )

    # ========================================================================
    # Hero
    # ========================================================================

    hero_heading: str = Field(
        default="",
        max_length=60,
    )

    hero_subheading: str = Field(
        default="",
        max_length=200,
    )

    hero_image_prompt: str = Field(
        default="",
        max_length=300,
        description=(
            "Описание на английском языке для генерации "
            "фоновой картинки hero-блока"
        ),
    )

    hero_image_url: str = Field(
        default="",
        max_length=1000,
    )

    # ========================================================================
    # О компании
    # ========================================================================

    about_short: str = Field(
        default="",
        max_length=300,
    )

    about_full: str = Field(
        default="",
        max_length=1000,
    )

    # ========================================================================
    # Features
    # ========================================================================

    features: list[Feature] = Field(
        default_factory=list,
        max_length=20,
    )

    # ========================================================================
    # Services
    # ========================================================================

    services: list[ServiceItem] = Field(
        default_factory=list,
        max_length=50,
    )

    # ========================================================================
    # Gallery
    # ========================================================================

    gallery: list[GalleryItem] = Field(
        default_factory=list,
        max_length=50,
    )

    # ========================================================================
    # Pricing
    # ========================================================================

    pricing: list[ServiceItem] = Field(
        default_factory=list,
        max_length=50,
    )

    # ========================================================================
    # Testimonials
    # ========================================================================

    testimonials: list[Testimonial] = Field(
        default_factory=list,
        max_length=50,
    )

    # ========================================================================
    # FAQ
    # ========================================================================

    faq: list[FAQItem] = Field(
        default_factory=list,
        max_length=50,
    )

    # ========================================================================
    # Team
    # ========================================================================

    team: list[TeamMember] = Field(
        default_factory=list,
        max_length=20,
    )

    # ========================================================================
    # Sidebar
    # ========================================================================

    sidebar_enabled: bool = Field(
        default=True,
    )

    sidebar_items: list[SidebarItem] = Field(
        default_factory=list,
        max_length=20,
    )

    sidebar_cta_text: str = Field(
        default="Забронировать",
        max_length=40,
    )

    # ========================================================================
    # Контакты
    # ========================================================================

    company_name: str = Field(
        default="",
        max_length=100,
    )

    company_address: str = Field(
        default="",
        max_length=200,
    )

    company_phone: str = Field(
        default="",
        max_length=30,
    )

    # ========================================================================
    # Booking
    # ========================================================================

    working_hours: str = Field(
        default="10:00-20:00",
        max_length=100,
    )

    booking_confirmation: str = Field(
        default="Спасибо! Ваша заявка принята.",
        max_length=300,
    )

    # ========================================================================
    # CTA
    # ========================================================================

    cta_text: str = Field(
        default="Записаться",
        max_length=100,
    )

    # ========================================================================
    # Footer
    # ========================================================================

    footer_text: str = Field(
        default="",
        max_length=100,
    )

    # ========================================================================
    # Валидаторы
    # ========================================================================

    @field_validator(
        "color_primary",
        "color_secondary",
        "color_bg",
        "color_text",
        "color_accent",
        mode="before",
    )
    @classmethod
    def fix_color(cls, value: Any) -> str:
        """
        Некорректный HEX заменяется на безопасный fallback.
        """
        return _normalize_color(value)

    @field_validator("template_name", mode="before")
    @classmethod
    def fix_template(cls, value: Any) -> str:
        """
        Неизвестный шаблон заменяется на template-modern.html.
        """
        if isinstance(value, str):
            value = value.strip()

            if value in ALLOWED_TEMPLATES:
                return value

        return DEFAULT_TEMPLATE

    @field_validator("palette_name", mode="before")
    @classmethod
    def normalize_palette_name(cls, value: Any) -> str:
        """
        Нормализует имя палитры, не отклоняя контент.
        """
        if not isinstance(value, str):
            return "auto"

        value = value.strip()

        if not value:
            return "auto"

        return value[:30]

    @field_validator("sidebar_items", mode="before")
    @classmethod
    def normalize_sidebar_items(cls, value: Any) -> list[Any]:
        """
        Защищает от некорректного значения sidebar_items.

        Если LLM вернула не список, используется пустой список,
        а render.py применит собственные значения по умолчанию.
        """
        if not isinstance(value, list):
            return []

        return value[:20]

    @field_validator("blocks", mode="before")
    @classmethod
    def normalize_blocks(cls, value: Any) -> list[str]:
        """
        Нормализует список блоков.
        """
        if not isinstance(value, list):
            return []

        result: list[str] = []

        for item in value[:30]:
            if isinstance(item, str):
                item = item.strip()

                if item:
                    result.append(item[:50])

        return result