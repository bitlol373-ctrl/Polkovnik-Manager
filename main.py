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

# The owner is learned from Telegram's business_connection update.
owner_user_id = None
away_mode = False
away_text = "Привет! Я сейчас не у телефона, отвечу, как только смогу."

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


def send_bot_message(chat_id: int, text: str):
    return telegram_call("sendMessage", {"chat_id": chat_id, "text": text})


def install_webhook():
    if not BOT_TOKEN or not PUBLIC_URL:
        log.info("Webhook is not installed yet: set TELEGRAM_BOT_TOKEN and PUBLIC_URL/RENDER_EXTERNAL_URL")
        return

    payload = {
        "url": f"{PUBLIC_URL}/telegram/webhook",
        "allowed_updates": [
            "message",
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
    return jsonify({
        "ok": True,
        "service": "Polkovnik Manager",
        "away_mode": away_mode,
    })


@app.post("/telegram/webhook")
def telegram_webhook():
    global owner_user_id, away_mode, away_text

    if WEBHOOK_SECRET:
        incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if incoming != WEBHOOK_SECRET:
            return jsonify({"ok": False}), 403

    update = request.get_json(silent=True) or {}
    log.info("Telegram update: %s", update)

    # Telegram tells us which user owns the connected Secretary bot.
    connection = update.get("business_connection")
    if connection:
        user = connection.get("user") or {}
        if user.get("id"):
            owner_user_id = user["id"]
            log.info("Secretary owner detected: %s", owner_user_id)

    # Commands are sent to the bot itself by the owner.
    command_message = update.get("message")
    if command_message:
        sender = command_message.get("from") or {}
        sender_id = sender.get("id")
        chat = command_message.get("chat") or {}
        chat_id = chat.get("id")
        text = (command_message.get("text") or "").strip()

        if chat_id and sender_id == owner_user_id and text:
            if text == "/start":
                send_bot_message(
                    chat_id,
                    "Polkovnik Manager готов.\n\n"
                    "/on — включить автоответчик\n"
                    "/off — выключить\n"
                    "/status — состояние\n"
                    "/text — показать текст\n"
                    "/text Новый текст — изменить текст",
                )
            elif text == "/on":
                away_mode = True
                send_bot_message(chat_id, "🟢 Автоответчик включён.")
            elif text == "/off":
                away_mode = False
                send_bot_message(chat_id, "🔴 Автоответчик выключен.")
            elif text == "/status":
                state = "🟢 включён" if away_mode else "🔴 выключен"
                send_bot_message(chat_id, f"Автоответчик: {state}\n\nТекст:\n{away_text}")
            elif text == "/text":
                send_bot_message(chat_id, f"Текущий текст:\n{away_text}")
            elif text.startswith("/text "):
                new_text = text[6:].strip()
                if new_text:
                    away_text = new_text
                    send_bot_message(chat_id, f"✅ Новый текст сохранён:\n{away_text}")

    # Messages received through Secretary.
    message = update.get("business_message")
    if message and away_mode:
        connection_id = message.get("business_connection_id")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = message.get("text") or message.get("caption")

        if connection_id and chat_id and text:
            try:
                send_business_message(
                    connection_id,
                    chat_id,
                    away_text,
                    message.get("message_id"),
                )
                log.info("Away reply sent to chat %s", chat_id)
            except Exception:
                log.exception("Failed to send away reply")

    return jsonify({"ok": True})


if __name__ == "__main__":
    install_webhook()
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
