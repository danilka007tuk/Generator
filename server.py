import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

app = FastAPI()

# Разрешаем запросы с любого сайта (наш сайт на GitHub Pages)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_TOKEN = os.getenv("MAX_BOT_TOKEN")
# ВАЖНО: домен platform-api2.max.ru (с цифрой 2) — актуальный адрес API MAX [citation:7][citation:12]
MAX_API_URL = "https://platform-api2.max.ru/messages"

# Хранилище подписчиков в памяти (для теста).
subscribers: set[int] = set()


class Booking(BaseModel):
    date: str
    name: str
    phone: str
    comment: str = ""


class Subscriber(BaseModel):
    user_id: int


async def send_max_message(user_id: int, text: str) -> bool:
    """Отправляет сообщение в MAX указанному пользователю."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            MAX_API_URL,
            params={"user_id": user_id},
            headers={"Authorization": MAX_TOKEN},
            json={"text": text},
        )
    # Логируем ответ MAX для диагностики
    print(f"MAX ответ: статус={response.status_code}, тело={response.text[:300]}")
    return response.status_code == 200

@app.post("/api/subscribe")
async def subscribe(sub: Subscriber):
    """Регистрирует подписчика — того, кто нажал /start у бота."""
    subscribers.add(sub.user_id)
    return {"ok": True, "total": len(subscribers)}


@app.post("/api/book")
async def create_booking(booking: Booking):
    """Принимает бронь с сайта и рассылает уведомления всем подписчикам."""
    text = (
        f"🔔 Новая бронь!\n\n"
        f"📅 Дата: {booking.date}\n"
        f"👤 Имя: {booking.name}\n"
        f"📞 Телефон: {booking.phone}\n"
        f"💬 Комментарий: {booking.comment or '—'}"
    )

    if not subscribers:
        return {"ok": True, "sent_to": 0}

    sent = 0
    for user_id in subscribers:
        if await send_max_message(user_id, text):
            sent += 1

    return {"ok": True, "sent_to": sent}


@app.get("/api/health")
async def health():
    return {"status": "ok", "subscribers": len(subscribers)}