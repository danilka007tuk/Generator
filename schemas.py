from pydantic import BaseModel, Field


class Feature(BaseModel):
    """Одно преимущество."""
    title: str = Field(max_length=60)
    description: str = Field(max_length=300)


class ServiceItem(BaseModel):
    """Одна услуга."""
    name: str = Field(max_length=80)
    description: str = Field(max_length=200)
    price: str = Field(default="", max_length=50)


class GalleryItem(BaseModel):
    """Один элемент галереи."""
    title: str = Field(max_length=80)
    description: str = Field(max_length=200)
    image_prompt: str = Field(default="", max_length=300)


class Testimonial(BaseModel):
    """Один отзыв."""
    author: str = Field(max_length=80)
    text: str = Field(max_length=300)
    rating: int = Field(default=5, ge=1, le=5)


class FAQItem(BaseModel):
    """Один вопрос-ответ."""
    question: str = Field(max_length=200)
    answer: str = Field(max_length=500)


class SidebarItem(BaseModel):
    """Один пункт сайдбара."""
    label: str = Field(max_length=40)
    anchor: str = Field(max_length=40)
    icon: str = Field(default="", max_length=10)


class TeamMember(BaseModel):
    """Один член команды."""
    name: str = Field(max_length=80)
    role: str = Field(max_length=80)
    description: str = Field(default="", max_length=200)


class SiteContent(BaseModel):
    """Все данные для сайта — генерируются ИИ."""

    # === Шаблон и палитра ===
    template_name: str = Field(default="template-modern.html", max_length=50)
    palette_name: str = Field(default="auto", max_length=20)

    # === Цвета (HEX) ===
    color_primary: str = Field(default="#667eea", max_length=20)
    color_secondary: str = Field(default="#764ba2", max_length=20)
    color_bg: str = Field(default="#f4f6fa", max_length=20)
    color_text: str = Field(default="#2c3e50", max_length=20)
    color_accent: str = Field(default="#667eea", max_length=20)

    # === Мета ===
    site_title: str = Field(max_length=60)
    meta_description: str = Field(max_length=160)

    # === Блоки ===
    blocks: list[str] = Field(
        default_factory=lambda: [
            "hero", "features", "booking", "services", "contacts", "cta"
        ]
    )

    # === Hero ===
    hero_heading: str = Field(max_length=60)
    hero_subheading: str = Field(max_length=200)
    hero_image_prompt: str = Field(
        default="", max_length=300,
        description="Описание для генерации фоновой картинки hero-блока"
    )

    # === О компании (новое) ===
    about_short: str = Field(
        default="", max_length=300,
        description="Короткое описание компании (2-3 предложения)"
    )
    about_full: str = Field(
        default="", max_length=1000,
        description="Полное описание компании для блока 'О нас'"
    )

    # === Features ===
    features: list[Feature] = Field(default_factory=list)

    # === Services ===
    services: list[ServiceItem] = Field(default_factory=list)

    # === Gallery ===
    gallery: list[GalleryItem] = Field(default_factory=list)

    # === Pricing ===
    pricing: list[ServiceItem] = Field(default_factory=list)

    # === Testimonials (новое) ===
    testimonials: list[Testimonial] = Field(default_factory=list)

    # === FAQ (новое) ===
    faq: list[FAQItem] = Field(default_factory=list)

    # === Team (новое) ===
    team: list[TeamMember] = Field(default_factory=list)

    # === Sidebar (новое) ===
    sidebar_enabled: bool = Field(
        default=True,
        description="Показывать ли сайдбар"
    )
    sidebar_items: list[SidebarItem] = Field(
        default_factory=list,
        description="Пункты меню в сайдбаре"
    )
    sidebar_cta_text: str = Field(
        default="Забронировать", max_length=40,
        description="Текст главной кнопки в сайдбаре"
    )

    # === Contacts ===
    company_name: str = Field(max_length=100)
    company_address: str = Field(max_length=200)
    company_phone: str = Field(max_length=30)

    # === Booking ===
    working_hours: str = Field(default="10:00-20:00", max_length=100)
    booking_confirmation: str = Field(max_length=300)

    # === CTA ===
    cta_text: str = Field(max_length=100)

    # === Footer ===
    footer_text: str = Field(max_length=100)