import os
import json
import logging
from flask import Flask, request, jsonify
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("polkovnik-manager")
app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()
SETTINGS_FILE = "/tmp/polkovnik_settings.json"

state = {"owner_user_id": None, "away_mode": False, "away_text": "Привет! Я сейчас не у телефона, отвечу, как только смогу.", "custom_texts": {}}


def load_state():
    global state
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            state.update(json.load(f))
    except Exception:
        pass


def save_state():
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        log.exception("Could not save state")


def tg(method, payload):
    with httpx.Client(timeout=30) as client:
        r = client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data)
        return data["result"]


def bot_msg(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg("sendMessage", payload)


def business_msg(connection_id, chat_id, text, reply_to=None):
    payload = {"business_connection_id": connection_id, "chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to}
    return tg("sendMessage", payload)


def menu():
    return {"inline_keyboard": [
        [{"text": "🟢 Включить", "callback_data": "on"}, {"text": "🔴 Выключить", "callback_data": "off"}],
        [{"text": "📊 Статус", "callback_data": "status"}, {"text": "📝 Стандартный текст", "callback_data": "text"}],
        [{"text": "👤 Персональный ответ", "callback_data": "set_help"}],
        [{"text": "📋 Персональные ответы", "callback_data": "list"}]
    ]}


def setup_webhook():
    if not BOT_TOKEN or not PUBLIC_URL:
        return
    payload = {"url": f"{PUBLIC_URL}/telegram/webhook", "allowed_updates": ["message", "callback_query", "business_connection", "business_message", "edited_business_message", "deleted_business_messages"]}
    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET
    tg("setWebhook", payload)
    log.info("Webhook installed")


@app.get("/")
def health():
    return jsonify({"ok": True, "service": "Polkovnik Manager", "away_mode": state["away_mode"]})


@app.post("/telegram/webhook")
def webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != WEBHOOK_SECRET:
        return jsonify({"ok": False}), 403
    update = request.get_json(silent=True) or {}
    log.info("Update received: %s", update)

    connection = update.get("business_connection")
    if connection and connection.get("user", {}).get("id"):
        state["owner_user_id"] = connection["user"]["id"]
        save_state()

    # Commands are accepted ONLY in the manager bot chat.
    msg = update.get("message")
    if msg:
        sender_id = (msg.get("from") or {}).get("id")
        chat_id = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if chat_id and sender_id == state["owner_user_id"] and text:
            if text == "/start":
                bot_msg(chat_id, "⚙️ Polkovnik Manager\n\nУправление автоответчиком:", menu())
            elif text == "/on":
                state["away_mode"] = True; save_state(); bot_msg(chat_id, "🟢 Автоответчик включён.", menu())
            elif text == "/off":
                state["away_mode"] = False; save_state(); bot_msg(chat_id, "🔴 Автоответчик выключен.", menu())
            elif text == "/status":
                bot_msg(chat_id, f"Автоответчик: {'🟢 включён' if state['away_mode'] else '🔴 выключен'}\nПерсональных ответов: {len(state['custom_texts'])}\n\nСтандартный:\n{state['away_text']}", menu())
            elif text == "/list":
                items = state["custom_texts"]
                if not items: bot_msg(chat_id, "📋 Персональных ответов нет.", menu())
                else: bot_msg(chat_id, "📋 Персональные ответы:\n\n" + "\n\n".join(f"ID {k}: {v}" for k, v in items.items()), menu())
            elif text == "/text":
                bot_msg(chat_id, f"📝 Стандартный текст:\n{state['away_text']}\n\nЧтобы изменить, отправь:\n/text Новый текст", menu())
            elif text.startswith("/text "):
                new_text = text[6:].strip()
                if new_text:
                    state["away_text"] = new_text; save_state(); bot_msg(chat_id, "✅ Стандартный текст сохранён.", menu())
            elif text.startswith("/set "):
                parts = text[5:].strip().split(maxsplit=1)
                if len(parts) == 2 and parts[0].lstrip("-").isdigit():
                    state["custom_texts"][parts[0]] = parts[1]; save_state(); bot_msg(chat_id, "✅ Персональный ответ сохранён.", menu())
                else:
                    bot_msg(chat_id, "Формат:\n/set ID текст\n\nНапример:\n/set 123456789 Я отвечу вечером.", menu())
            elif text.startswith("/del "):
                target = text[5:].strip()
                if target in state["custom_texts"]:
                    del state["custom_texts"][target]; save_state(); bot_msg(chat_id, "✅ Персональный ответ удалён.", menu())
                else: bot_msg(chat_id, "Такого персонального ответа нет.", menu())

    # Inline-button callbacks are also handled only in manager bot chat.
    cb = update.get("callback_query")
    if cb:
        sender_id = (cb.get("from") or {}).get("id")
        chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = cb.get("data")
        if sender_id == state["owner_user_id"] and chat_id:
            tg("answerCallbackQuery", {"callback_query_id": cb["id"]})
            if data == "on": state["away_mode"] = True; save_state(); bot_msg(chat_id, "🟢 Автоответчик включён.", menu())
            elif data == "off": state["away_mode"] = False; save_state(); bot_msg(chat_id, "🔴 Автоответчик выключен.", menu())
            elif data == "status": bot_msg(chat_id, f"Автоответчик: {'🟢 включён' if state['away_mode'] else '🔴 выключен'}\nПерсональных ответов: {len(state['custom_texts'])}", menu())
            elif data == "text": bot_msg(chat_id, f"📝 Стандартный текст:\n{state['away_text']}\n\nДля изменения:\n/text Новый текст", menu())
            elif data == "list":
                items = state["custom_texts"]
                bot_msg(chat_id, "📋 Нет персональных ответов." if not items else "📋 Персональные ответы:\n\n" + "\n\n".join(f"ID {k}: {v}" for k, v in items.items()), menu())
            elif data == "set_help":
                bot_msg(chat_id, "👤 Настройка персонального ответа\n\nОтправь мне в ЭТОМ чате:\n/set ID текст\n\nНапример:\n/set 123456789 Не могу сейчас говорить, напишу позже.\n\nВ чат с человеком заходить не нужно.", menu())

    business = update.get("business_message")
    if business and state["away_mode"]:
        connection_id = business.get("business_connection_id")
        chat_id = (business.get("chat") or {}).get("id")
        if connection_id and chat_id and (business.get("text") or business.get("caption")):
            reply = state["custom_texts"].get(str(chat_id), state["away_text"])
            try:
                business_msg(connection_id, chat_id, reply, business.get("message_id"))
            except Exception:
                log.exception("Failed to send business reply")

    return jsonify({"ok": True})


if __name__ == "__main__":
    load_state()
    setup_webhook()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
