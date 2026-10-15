from pathlib import Path
import bleach
from jinja2 import Environment, FileSystemLoader, select_autoescape
from schemas import SiteContent


# Папка templates рядом с render.py
TEMPLATES_DIR = Path(__file__).parent / "templates"

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

ALLOWED_TAGS = ["b", "i", "br", "a"]
ALLOWED_ATTRS = {"a": ["href"]}


def clean(text: str) -> str:
    if not isinstance(text, str):
        return ""
    return bleach.clean(text, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)


def render_site(content: SiteContent) -> str:
    """Подставляет данные в шаблон index.html."""
    data = content.model_dump()

    # Чистим все текстовые поля от опасных тегов
    for key in ["site_title", "meta_description", "hero_heading", "hero_subheading",
                "company_name", "company_address", "company_phone",
                "working_hours", "booking_confirmation", "cta_text", "footer_text"]:
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

    template = env.get_template("index.html")
    return template.render(**data)


def save_site(html: str, output_dir: str = "output") -> Path:
    out = Path(output_dir)
    out.mkdir(exist_ok=True)
    index = out / "index.html"
    index.write_text(html, encoding="utf-8")
    return index