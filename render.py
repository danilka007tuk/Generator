from pathlib import Path
import bleach
from jinja2 import Environment, FileSystemLoader, select_autoescape
from schemas import SiteContent


TEMPLATES_DIR = Path(__file__).parent / "templates"

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

ALLOWED_TAGS = ["b", "i", "br", "a"]
ALLOWED_ATTRS = {"a": ["href"]}

ALLOWED_TEMPLATES = [
    "template-modern.html",
    "template-light.html",
    "template-warm.html",
    "template-bold.html",
]


def clean(text: str) -> str:
    """Очищает текст от опасных HTML-тегов."""
    if not isinstance(text, str):
        return ""
    return bleach.clean(text, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)


def clean_color(hex_color: str, default: str) -> str:
    """Проверяет, что цвет — валидный HEX. Иначе возвращает default."""
    if not isinstance(hex_color, str):
        return default
    hex_color = hex_color.strip()
    if not hex_color.startswith("#"):
        return default
    if len(hex_color) not in (4, 7):
        return default
    try:
        int(hex_color[1:], 16)
    except ValueError:
        return default
    return hex_color


def render_site(content: SiteContent) -> str:
    """Подставляет данные в выбранный шаблон."""
    data = content.model_dump()

    # Чистим текстовые поля
    for key in [
        "site_title", "meta_description", "hero_heading", "hero_subheading",
        "company_name", "company_address", "company_phone",
        "working_hours", "booking_confirmation", "cta_text", "footer_text",
    ]:
        if key in data:
            data[key] = clean(data[key])

    for feature in data.get("features", []):
        feature["title"] = clean(feature.get("title", ""))
        feature["description"] = clean(feature.get("description", ""))

    for service in data.get("services", []):
        service["name"] = clean(service.get("name", ""))
        service["description"] = clean(service.get("description", ""))
        service["price"] = clean(service.get("price", ""))

    for item in data.get("gallery", []):
        item["title"] = clean(item.get("title", ""))
        item["description"] = clean(item.get("description", ""))

    for item in data.get("pricing", []):
        item["name"] = clean(item.get("name", ""))
        item["description"] = clean(item.get("description", ""))
        item["price"] = clean(item.get("price", ""))

    # Чистим цвета
    data["color_primary"] = clean_color(data.get("color_primary", ""), "#667eea")
    data["color_secondary"] = clean_color(data.get("color_secondary", ""), "#764ba2")
    data["color_bg"] = clean_color(data.get("color_bg", ""), "#f4f6fa")
    data["color_text"] = clean_color(data.get("color_text", ""), "#2c3e50")
    data["color_accent"] = clean_color(data.get("color_accent", ""), "#667eea")

    # Выбираем шаблон
    template_name = data.get("template_name", "template-modern.html")
    if template_name not in ALLOWED_TEMPLATES:
        template_name = "template-modern.html"

    template = env.get_template(template_name)
    return template.render(**data)


def save_site(html: str, output_dir: str = "output") -> Path:
    """Сохраняет готовый HTML в файл output/index.html."""
    out = Path(output_dir)
    out.mkdir(exist_ok=True)
    index = out / "index.html"
    index.write_text(html, encoding="utf-8")
    return index