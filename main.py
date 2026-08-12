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
owner_user_id = None
away_mode = False
away_text = "Привет! Я сейчас не у телефона, отвечу, как только смогу."
custom_texts = {}

if not BOT_TOKEN:
    log.warning("TELEGRAM_BOT_TOKEN is not set")

def telegram_url(method):
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

def telegram_call(method, payload):
    with httpx.Client(timeout=30) as client:
        response = client.post(telegram_url(method), json=payload)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data["result"]

def send_business_message(connection_id, chat_id, text, reply_to=None):
    payload = {"business_connection_id": connection_id, "chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to}
    return telegram_call("sendMessage", payload)

def send_bot_message(chat_id, text):
    return telegram_call("sendMessage", {"chat_id": chat_id, "text": text})

def install_webhook():
    if not BOT_TOKEN or not PUBLIC_URL:
        log.info("Webhook is not installed yet")
        return
    payload = {
        "url": f"{PUBLIC_URL}/telegram/webhook",
        "allowed_updates": ["message", "business_connection", "business_message", "edited_business_message", "deleted_business_messages"],
        "drop_pending_updates": False,
    }
    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET
    log.info("Telegram webhook installed: %s", telegram_call("setWebhook", payload))

@app.get("/")
def health():
    return jsonify({"ok": True, "service": "Polkovnik Manager", "away_mode": away_mode})

@app.post("/telegram/webhook")
def telegram_webhook():
    global owner_user_id, away_mode, away_text, custom_texts
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != WEBHOOK_SECRET:
        return jsonify({"ok": False}), 403
    update = request.get_json(silent=True) or {}
    log.info("Telegram update: %s", update)

    connection = update.get("business_connection")
    if connection:
        user = connection.get("user") or {}
        if user.get("id"):
            owner_user_id = user["id"]

    command_message = update.get("message")
    if command_message:
        sender_id = (command_message.get("from") or {}).get("id")
        chat_id = (command_message.get("chat") or {}).get("id")
        text = (command_message.get("text") or "").strip()
        if chat_id and sender_id == owner_user_id and text:
            if text == "/start":
                send_bot_message(chat_id, "Polkovnik Manager готов.\n\n/on — включить\n/off — выключить\n/status — состояние\n/text — стандартный текст\n/text Новый текст — изменить стандартный\n\n/set ID текст — персональный ответ\n/del ID — удалить персональный\n/list — список персональных")
            elif text == "/on":
                away_mode = True
                send_bot_message(chat_id, "🟢 Автоответчик включён.")
            elif text == "/off":
                away_mode = False
                send_bot_message(chat_id, "🔴 Автоответчик выключен.")
            elif text == "/status":
                state = "🟢 включён" if away_mode else "🔴 выключен"
                send_bot_message(chat_id, f"Автоответчик: {state}\n\nСтандартный текст:\n{away_text}\n\nПерсональных ответов: {len(custom_texts)}")
            elif text == "/text":
                send_bot_message(chat_id, f"Стандартный текст:\n{away_text}")
            elif text.startswith("/text "):
                new_text = text[6:].strip()
                if new_text:
                    away_text = new_text
                    send_bot_message(chat_id, f"✅ Стандартный текст сохранён:\n{away_text}")
            elif text.startswith("/set "):
                parts = text[5:].strip().split(maxsplit=1)
                if len(parts) != 2 or not parts[0].lstrip("-").isdigit():
                    send_bot_message(chat_id, "Формат: /set ID текст\nНапример: /set 123456789 Я сейчас занят, отвечу вечером.")
                else:
                    target_id, custom = parts[0], parts[1].strip()
                    custom_texts[target_id] = custom
                    send_bot_message(chat_id, f"✅ Персональный ответ для {target_id} сохранён:\n{custom}")
            elif text.startswith("/del "):
                target_id = text[5:].strip()
                if target_id in custom_texts:
                    del custom_texts[target_id]
                    send_bot_message(chat_id, f"✅ Ответ для {target_id} удалён. Теперь используется стандартный.")
                else:
                    send_bot_message(chat_id, "Для этого ID персонального ответа нет.")
            elif text == "/list":
                if not custom_texts:
                    send_bot_message(chat_id, "Персональных ответов пока нет.")
                else:
                    send_bot_message(chat_id, "Персональные ответы:\n\n" + "\n\n".join(f"{k}: {v}" for k, v in custom_texts.items()))

    message = update.get("business_message")
    if message and away_mode:
        connection_id = message.get("business_connection_id")
        chat_id = (message.get("chat") or {}).get("id")
        text = message.get("text") or message.get("caption")
        if connection_id and chat_id and text:
            reply = custom_texts.get(str(chat_id), away_text)
            try:
                send_business_message(connection_id, chat_id, reply, message.get("message_id"))
            except Exception:
                log.exception("Failed to send away reply")
    return jsonify({"ok": True})

if __name__ == "__main__":
    install_webhook()
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
