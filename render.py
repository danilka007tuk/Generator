# render.py
from pathlib import Path
import os
import uuid
import base64
import bleach
import logging
from jinja2 import Environment, FileSystemLoader, select_autoescape
from openai import OpenAI
from dotenv import load_dotenv
from schemas import SiteContent

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
OUTPUT_DIR = Path(__file__).parent / "output"
IMAGES_DIR = OUTPUT_DIR / "images"

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

DEFAULT_SIDEBAR_ITEMS = [
    {"label": "Услуги", "anchor": "#services", "icon": "💼"},
    {"label": "Галерея", "anchor": "#gallery", "icon": "🖼"},
    {"label": "Отзывы", "anchor": "#testimonials", "icon": "⭐"},
    {"label": "Контакты", "anchor": "#contacts", "icon": "📞"},
]

# Таймаут увеличен до 120 секунд, так как генерация картинок иногда занимает время
provod_client = OpenAI(
    api_key=os.getenv("PROVOD_API_KEY"),
    base_url="https://api.provod.ai/v1",
    timeout=120.0,
)


def generate_image_provod(prompt: str, width: int = 1024, height: int = 1024) -> str:
    if not prompt or not isinstance(prompt, str):
        logger.warning("Пустой промпт для картинки")
        return ""

    prompt = prompt.strip()[:300]
    if not prompt:
        logger.warning("Промпт для картинки пустой после очистки")
        return ""

    try:
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)

        logger.info(f"Генерация картинки: {prompt[:50]}...")

        # Убрали response_format="b64_json", так как gpt-image-2 возвращает его по умолчанию
        # Используем размер 1024x1024, который точно поддерживается
        response = provod_client.images.generate(
            model="openai/gpt-image-2",
            prompt=prompt,
            size=f"{width}x{height}",
            n=1,
        )

        b64 = response.data[0].b64_json
        if not b64:
            logger.warning("provod.ai вернул пустой base64")
            return ""

        filename = f"{uuid.uuid4().hex}.png"
        filepath = IMAGES_DIR / filename
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(b64))

        logger.info(f"Картинка сохранена: {filename}")
        return f"images/{filename}"
    except Exception as e:
        logger.error(f"Ошибка генерации картинки: {e}")
        return ""


def get_sidebar_items(content: SiteContent) -> list[dict]:
    items = content.sidebar_items or []
    result = []
    for item in items:
        d = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        label = d.get("label", "").strip()
        anchor = d.get("anchor", "").strip()
        icon = d.get("icon", "").strip()
        if not label or not anchor:
            continue
        if not anchor.startswith("#"):
            anchor = "#" + anchor
        result.append({"label": label, "anchor": anchor, "icon": icon})
    if not result:
        result = [dict(x) for x in DEFAULT_SIDEBAR_ITEMS]
    return result


def clean(text: str) -> str:
    if not isinstance(text, str):
        return ""
    return bleach.clean(text, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)


def clean_color(hex_color: str, default: str) -> str:
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


def clean_anchor(anchor: str) -> str:
    if not isinstance(anchor, str):
        return "#"
    anchor = anchor.strip()
    if not anchor.startswith("#"):
        anchor = "#" + anchor
    safe = "".join(c for c in anchor if c.isalnum() or c in "-#_")
    return safe if safe != "#" else "#"


def render_site(content: SiteContent, server_url: str = "", site_id: str = "") -> str:
    data = content.model_dump()
    data["server_url"] = server_url or "http://127.0.0.1:8001"
    data["site_id"] = site_id or "site"

    for key in [
        "site_title", "meta_description", "hero_heading", "hero_subheading",
        "company_name", "company_address", "company_phone",
        "working_hours", "booking_confirmation", "cta_text", "footer_text",
        "about_short", "about_full", "sidebar_cta_text",
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
        prompt = item.get("image_prompt", "") or item.get("title", "") or "Beauty salon interior"
        item["image_url"] = generate_image_provod(prompt, width=1024, height=768)

    for item in data.get("pricing", []):
        item["name"] = clean(item.get("name", ""))
        item["description"] = clean(item.get("description", ""))
        item["price"] = clean(item.get("price", ""))

    for testimonial in data.get("testimonials", []):
        testimonial["author"] = clean(testimonial.get("author", ""))
        testimonial["text"] = clean(testimonial.get("text", ""))

    for faq in data.get("faq", []):
        faq["question"] = clean(faq.get("question", ""))
        faq["answer"] = clean(faq.get("answer", ""))

    for member in data.get("team", []):
        member["name"] = clean(member.get("name", ""))
        member["role"] = clean(member.get("role", ""))
        member["description"] = clean(member.get("description", ""))
        member["services"] = [clean(s) for s in member.get("services", [])]

    data["sidebar_items"] = get_sidebar_items(content)

    # Генерация главной картинки (hero)
    hero_prompt = data.get("hero_image_prompt", "")
    if not hero_prompt:
        hero_prompt = f"Beautiful interior for {data.get('company_name', 'business')}, photorealistic"
    # Для hero используем размер 1024x1024, чтобы избежать ошибки 502 от provod.ai
    data["hero_image_url"] = generate_image_provod(hero_prompt, width=1024, height=1024)

    data["color_primary"] = clean_color(data.get("color_primary", ""), "#667eea")
    data["color_secondary"] = clean_color(data.get("color_secondary", ""), "#764ba2")
    data["color_bg"] = clean_color(data.get("color_bg", ""), "#f4f6fa")
    data["color_text"] = clean_color(data.get("color_text", ""), "#2c3e50")
    data["color_accent"] = clean_color(data.get("color_accent", ""), "#667eea")

    template_name = data.get("template_name", "template-modern.html")
    if template_name not in ALLOWED_TEMPLATES:
        template_name = "template-modern.html"

    template = env.get_template(template_name)
    html = template.render(**data)

    # Редирект на регистрацию
    if "<head>" in html:
        redirect_script = """<script>if (!localStorage.getItem('user_email') && !window.location.search.includes('verified=1')) { window.location.replace('register.html'); }</script>"""
        html = html.replace("<head>", f"<head>{redirect_script}", 1)

    return html


def render_register_page(content: SiteContent, server_url: str = "", site_id: str = "") -> str:
    data = content.model_dump()
    data["server_url"] = server_url or "http://127.0.0.1:8001"
    data["site_id"] = site_id or "site"

    for key in ["site_title", "company_name"]:
        if key in data:
            data[key] = clean(data[key])

    data["color_primary"] = clean_color(data.get("color_primary", ""), "#667eea")
    data["color_secondary"] = clean_color(data.get("color_secondary", ""), "#764ba2")
    data["color_bg"] = clean_color(data.get("color_bg", ""), "#f4f6fa")
    data["color_text"] = clean_color(data.get("color_text", ""), "#2c3e50")
    data["color_accent"] = clean_color(data.get("color_accent", ""), "#667eea")

    template = env.get_template("register.html")
    return template.render(**data)


def save_site(html: str, filename: str = "index.html", output_dir: str = "output") -> Path:
    out = Path(output_dir)
    out.mkdir(exist_ok=True)
    index = out / filename
    index.write_text(html, encoding="utf-8")
    return index