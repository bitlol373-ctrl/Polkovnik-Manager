import os
import logging
from flask import Flask, request, jsonify
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("polkovnik-manager")

app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")

if not BOT_TOKEN:
    log.warning("TELEGRAM_BOT_TOKEN is not set")


def telegram_url(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"


def telegram_call(method: str, payload: dict):
    with httpx.Client(timeout=30) as client:
        response = client.post(telegram_url(method), json=payload)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data["result"]


def send_business_message(connection_id: str, chat_id: int, text: str, reply_to: int | None = None):
    payload = {
        "business_connection_id": connection_id,
        "chat_id": chat_id,
        "text": text,
    }
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to}
    return telegram_call("sendMessage", payload)


def install_webhook():
    if not BOT_TOKEN or not PUBLIC_URL:
        log.info("Webhook is not installed yet: set TELEGRAM_BOT_TOKEN and PUBLIC_URL/RENDER_EXTERNAL_URL")
        return

    payload = {
        "url": f"{PUBLIC_URL}/telegram/webhook",
        "allowed_updates": [
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages",
        ],
        "drop_pending_updates": False,
    }
    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET

    result = telegram_call("setWebhook", payload)
    log.info("Telegram webhook installed: %s", result)


@app.get("/")
def health():
    return jsonify({"ok": True, "service": "Polkovnik Manager"})


@app.post("/telegram/webhook")
def telegram_webhook():
    if WEBHOOK_SECRET:
        incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if incoming != WEBHOOK_SECRET:
            return jsonify({"ok": False}), 403

    update = request.get_json(silent=True) or {}
    log.info("Telegram update: %s", update)

    message = update.get("business_message")
    if message:
        connection_id = message.get("business_connection_id")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = message.get("text") or message.get("caption")

        if connection_id and chat_id and text:
            try:
                send_business_message(
                    connection_id,
                    chat_id,
                    "Секретарь подключён. Сейчас я ещё настраиваюсь 🤖",
                    message.get("message_id"),
                )
                log.info("Replied to chat %s", chat_id)
            except Exception:
                log.exception("Failed to send business message")

    return jsonify({"ok": True})


if __name__ == "__main__":
    install_webhook()
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
