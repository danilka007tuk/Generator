from pydantic import BaseModel, Field


class Feature(BaseModel):
    title: str = Field(max_length=60)
    description: str = Field(max_length=300)


class ServiceItem(BaseModel):
    name: str = Field(max_length=80)
    description: str = Field(max_length=200)
    price: str = Field(default="", max_length=50)


class GalleryItem(BaseModel):
    title: str = Field(max_length=80)
    description: str = Field(max_length=200)


class SiteContent(BaseModel):
    """Все данные для сайта — генерируются ИИ."""

    # Мета
    site_title: str = Field(max_length=60)
    meta_description: str = Field(max_length=160)

    # Какие блоки показывать и в каком порядке
    blocks: list[str] = Field(
        default_factory=lambda: [
            "hero", "features", "booking", "services", "contacts", "cta"
        ]
    )

    # Блок hero
    hero_heading: str = Field(max_length=60)
    hero_subheading: str = Field(max_length=200)

    # Блок features
    features: list[Feature] = Field(default_factory=list)

    # Блок services
    services: list[ServiceItem] = Field(default_factory=list)

    # Блок gallery
    gallery: list[GalleryItem] = Field(default_factory=list)

    # Блок pricing
    pricing: list[ServiceItem] = Field(default_factory=list)

    # Блок contacts
    company_name: str = Field(max_length=100)
    company_address: str = Field(max_length=200)
    company_phone: str = Field(max_length=30)

    # Блок booking
    working_hours: str = Field(default="10:00-20:00", max_length=100)
    booking_confirmation: str = Field(max_length=300)

    # Блок cta
    cta_text: str = Field(max_length=100)

    # Футер
    footer_text: str = Field(max_length=100)