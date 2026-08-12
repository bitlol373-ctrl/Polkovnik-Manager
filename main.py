import os
import time
import logging
from flask import Flask, request, jsonify
import httpx
from supabase import create_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("polkovnik-manager")
app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

state = {"owner_user_id": None, "away_mode": False, "timer_until": None, "away_text": "Привет! Я сейчас не у телефона, отвечу, как только смогу."}
reply_cooldowns = {}
REPLY_COOLDOWN = 5 * 60
notifications_enabled = True
last_manager_message_id = None
bot_user_id = None


def load_state():
    global state, notifications_enabled
    if not supabase:
        return
    try:
        row = supabase.table("manager_state").select("*").eq("id", 1).single().execute().data
        if row:
            state["owner_user_id"] = row.get("owner_user_id")
            state["away_mode"] = bool(row.get("away_mode", False))
            state["away_text"] = row.get("away_text") or state["away_text"]
            notifications_enabled = row.get("notifications_enabled", True) is not False
            timer = row.get("timer_until")
            if timer:
                from datetime import datetime, timezone
                state["timer_until"] = datetime.fromisoformat(timer.replace("Z", "+00:00")).timestamp()
    except Exception:
        log.exception("Could not load state from Supabase")


def save_state():
    if not supabase:
        return False
    try:
        from datetime import datetime, timezone
        timer = datetime.fromtimestamp(state["timer_until"], timezone.utc).isoformat() if state.get("timer_until") else None
        data = {"owner_user_id": state.get("owner_user_id"), "away_mode": bool(state.get("away_mode")), "away_text": state.get("away_text"), "timer_until": timer}
        try:
            data["notifications_enabled"] = notifications_enabled
            supabase.table("manager_state").update(data).eq("id", 1).execute()
        except Exception:
            data.pop("notifications_enabled", None)
            supabase.table("manager_state").update(data).eq("id", 1).execute()
        return True
    except Exception:
        log.exception("SUPABASE WRITE ERROR (manager_state)")
        return False


def get_custom_texts():
    if not supabase:
        return {}
    try:
        rows = supabase.table("custom_replies").select("chat_id,reply_text").execute().data or []
        return {str(r["chat_id"]): r["reply_text"] for r in rows}
    except Exception:
        log.exception("SUPABASE READ ERROR (custom_replies)")
        return {}


def set_custom_text(chat_id, text):
    try:
        supabase.table("custom_replies").upsert({"chat_id": int(chat_id), "reply_text": text}).execute()
        return True, None
    except Exception as e:
        return False, str(e)


def delete_custom_text(chat_id):
    try:
        supabase.table("custom_replies").delete().eq("chat_id", int(chat_id)).execute()
        return True, None
    except Exception as e:
        return False, str(e)


def timer_active():
    until = state.get("timer_until")
    if until is not None and time.time() >= until:
        state["away_mode"] = False
        state["timer_until"] = None
        save_state()
        return False
    return bool(state.get("away_mode"))


def timer_label():
    until = state.get("timer_until")
    if not until:
        return "без таймера"
    left = max(0, int(until - time.time()))
    hours, rem = divmod(left, 3600)
    minutes = rem // 60
    return f"до окончания: {hours} ч {minutes} мин" if hours else f"до окончания: {minutes} мин"


def parse_duration(value):
    import re
    matches = re.findall(r"(\d+)\s*([hm])", value.lower())
    if not matches:
        return None
    total = sum(int(a) * (3600 if u == "h" else 60) for a, u in matches)
    return total if total > 0 else None


def format_duration(seconds):
    hours, rem = divmod(int(seconds), 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин"


def tg(method, payload):
    with httpx.Client(timeout=30) as client:
        r = client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data)
        return data["result"]


def bot_msg(chat_id, text, reply_markup=None, keep_last=False):
    global last_manager_message_id
    if keep_last and last_manager_message_id:
        try:
            tg("deleteMessage", {"chat_id": chat_id, "message_id": last_manager_message_id})
        except Exception as e:
            log.warning("Could not delete previous manager message %s: %s", last_manager_message_id, e)
        last_manager_message_id = None
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    result = tg("sendMessage", payload)
    if keep_last:
        last_manager_message_id = result.get("message_id")
    return result


def business_msg(connection_id, chat_id, text, reply_to=None):
    payload = {"business_connection_id": connection_id, "chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to}
    return tg("sendMessage", payload)


def mark_business_read(connection_id, chat_id):
    try:
        tg("readBusinessMessage", {"business_connection_id": connection_id, "chat_id": chat_id})
        return True
    except Exception:
        log.exception("Could not mark business chat as read: chat_id=%s", chat_id)
        return False


def menu():
    return {"inline_keyboard": [
        [{"text": "🟢 Включить", "callback_data": "on"}, {"text": "🔴 Выключить", "callback_data": "off"}],
        [{"text": "⏱ Таймер", "callback_data": "timer"}, {"text": "📊 Статус", "callback_data": "status"}],
        [{"text": "📝 Стандартный текст", "callback_data": "text"}],
        [{"text": "👤 Персональный ответ", "callback_data": "set_help"}],
        [{"text": "📋 Персональные ответы", "callback_data": "list"}],
        [{"text": "🔔 Уведомления", "callback_data": "notify"}]
    ]}


def timer_menu():
    return {"inline_keyboard": [
        [{"text": "30 минут", "callback_data": "t:1800"}, {"text": "1 час", "callback_data": "t:3600"}],
        [{"text": "2 часа", "callback_data": "t:7200"}, {"text": "4 часа", "callback_data": "t:14400"}],
        [{"text": "8 часов", "callback_data": "t:28800"}],
        [{"text": "♾ Без таймера", "callback_data": "t:0"}, {"text": "❌ Остановить", "callback_data": "off"}],
        [{"text": "⬅️ Назад", "callback_data": "back"}]
    ]}


def setup_webhook():
    global bot_user_id
    if not BOT_TOKEN or not PUBLIC_URL:
        return
    try:
        me = tg("getMe", {})
        bot_user_id = me.get("id")
        log.info("Manager bot id: %s", bot_user_id)
    except Exception:
        log.exception("Could not get Manager bot id")
    payload = {"url": f"{PUBLIC_URL}/telegram/webhook", "allowed_updates": ["message", "callback_query", "business_connection", "business_message", "edited_business_message", "deleted_business_messages"]}
    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET
    tg("setWebhook", payload)


@app.get("/")
def health():
    return jsonify({"ok": True, "service": "Polkovnik Manager", "away_mode": timer_active(), "timer": timer_label()})


@app.post("/telegram/webhook")
def webhook():
    global notifications_enabled
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != WEBHOOK_SECRET:
        return jsonify({"ok": False}), 403
    update = request.get_json(silent=True) or {}
    log.info("Update received: %s", update)

    connection = update.get("business_connection")
    if connection and connection.get("user", {}).get("id"):
        state["owner_user_id"] = connection["user"]["id"]
        save_state()

    msg = update.get("message")
    if msg:
        sender_id = (msg.get("from") or {}).get("id")
        chat_id = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if chat_id and sender_id and text == "/start" and state["owner_user_id"] is None:
            state["owner_user_id"] = sender_id
            save_state()
            bot_msg(chat_id, "⚙️ Polkovник Manager\n\nТы назначен владельцем. Управление автоответчиком:", menu(), keep_last=True)
        elif chat_id and str(sender_id) == str(state["owner_user_id"]) and text:
            custom = get_custom_texts()
            if text == "/start": bot_msg(chat_id, "⚙️ Polkovник Manager\n\nУправление автоответчиком:", menu(), keep_last=True)
            elif text == "/on": state.update({"away_mode": True, "timer_until": None}); save_state(); bot_msg(chat_id, "🟢 Автоответчик включён без таймера.", menu(), keep_last=True)
            elif text == "/off": state.update({"away_mode": False, "timer_until": None}); save_state(); bot_msg(chat_id, "🔴 Автоответчик выключен.", menu(), keep_last=True)
            elif text.startswith("/timer"):
                parts = text.split(maxsplit=1)
                if len(parts) == 1: bot_msg(chat_id, f"⏱ Таймер: {timer_label()}", timer_menu(), keep_last=True)
                else:
                    duration = parse_duration(parts[1])
                    if duration is None: bot_msg(chat_id, "Формат: /timer 30m, /timer 2h или /timer 1h30m", timer_menu(), keep_last=True)
                    else: state.update({"away_mode": True, "timer_until": time.time()+duration}); save_state(); bot_msg(chat_id, f"⏱ Автоответчик включён на {format_duration(duration)}.", menu(), keep_last=True)
            elif text == "/status": bot_msg(chat_id, f"Автоответчик: {'🟢 включён' if timer_active() else '🔴 выключен'}\nТаймер: {timer_label()}\nУведомления: {'🟢 включены' if notifications_enabled else '🔴 выключены'}\nПерсональных ответов: {len(custom)}\n\nСтандартный:\n{state['away_text']}", menu(), keep_last=True)
            elif text == "/list": bot_msg(chat_id, "📋 Нет персональных ответов." if not custom else "📋 Персональные ответы:\n\n" + "\n\n".join(f"ID {k}: {v}" for k,v in custom.items()), menu(), keep_last=True)
            elif text == "/text": bot_msg(chat_id, f"📝 Стандартный текст:\n{state['away_text']}\n\n/text Новый текст", menu(), keep_last=True)
            elif text.startswith("/text "):
                new_text = text[6:].strip()
                state["away_text"] = new_text
                if new_text and save_state(): bot_msg(chat_id, "✅ Стандартный текст сохранён.", menu(), keep_last=True)
            elif text.startswith("/set "):
                parts = text[5:].strip().split(maxsplit=1)
                if len(parts) == 2 and parts[0].lstrip("-").isdigit():
                    ok, error = set_custom_text(parts[0], parts[1]); bot_msg(chat_id, "✅ Персональный ответ сохранён." if ok else f"❌ Ошибка Supabase:\n{error}", menu(), keep_last=True)
                else: bot_msg(chat_id, "Формат: /set ID текст", menu(), keep_last=True)
            elif text.startswith("/del "):
                ok, error = delete_custom_text(text[5:].strip()); bot_msg(chat_id, "✅ Персональный ответ удалён." if ok else f"❌ Ошибка Supabase:\n{error}", menu(), keep_last=True)

    cb = update.get("callback_query")
    if cb:
        sender_id = (cb.get("from") or {}).get("id")
        chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = cb.get("data")
        if str(sender_id) == str(state["owner_user_id"]) and chat_id:
            tg("answerCallbackQuery", {"callback_query_id": cb["id"]})
            if data == "on": state.update({"away_mode": True, "timer_until": None}); save_state(); bot_msg(chat_id, "🟢 Включено.", menu(), keep_last=True)
            elif data == "off": state.update({"away_mode": False, "timer_until": None}); save_state(); bot_msg(chat_id, "🔴 Выключено.", menu(), keep_last=True)
            elif data == "timer": bot_msg(chat_id, f"⏱ Таймер: {timer_label()}", timer_menu(), keep_last=True)
            elif data.startswith("t:"):
                seconds = int(data.split(":",1)[1]); state["away_mode"] = True; state["timer_until"] = time.time()+seconds if seconds else None; save_state(); bot_msg(chat_id, "⏱ Таймер установлен.", menu(), keep_last=True)
            elif data == "back": bot_msg(chat_id, "⚙️ Управление:", menu(), keep_last=True)
            elif data == "status": bot_msg(chat_id, f"Автоответчик: {'🟢 включён' if timer_active() else '🔴 выключен'}\nТаймер: {timer_label()}\nУведомления: {'🟢 включены' if notifications_enabled else '🔴 выключены'}\nПерсональных ответов: {len(get_custom_texts())}", menu(), keep_last=True)
            elif data == "text": bot_msg(chat_id, f"📝 Стандартный текст:\n{state['away_text']}\n\nДля изменения:\n/text Новый текст", menu(), keep_last=True)
            elif data == "list":
                custom = get_custom_texts(); bot_msg(chat_id, "📋 Нет персональных ответов." if not custom else "📋 Персональные ответы:\n\n"+"\n\n".join(f"ID {k}: {v}" for k,v in custom.items()), menu(), keep_last=True)
            elif data == "set_help": bot_msg(chat_id, "👤 Персональный ответ\n\nВ этом чате отправь:\n/set ID текст", menu(), keep_last=True)
            elif data == "notify":
                notifications_enabled = not notifications_enabled; save_state(); bot_msg(chat_id, f"🔔 Уведомления {'включены 🟢' if notifications_enabled else 'выключены 🔴'}.", menu(), keep_last=True)

    business = update.get("business_message")
    if business and timer_active():
        connection_id = business.get("business_connection_id")
        chat = business.get("chat") or {}
        chat_id = chat.get("id")
        sender = business.get("from") or {}
        sender_id = sender.get("id")
        sender_business_bot = business.get("sender_business_bot")

        # Важно: исходящие сообщения, отправленные самим Manager/Business-ботом,
        # тоже могут приходить как business_message. Их нельзя превращать в новые
        # уведомления и автоответы, иначе получается бесконечный цикл.
        is_owner = (
            str(sender_id) == str(state.get("owner_user_id"))
            or str(chat_id) == str(state.get("owner_user_id"))
            or isinstance(sender_business_bot, dict)
            or bool(sender.get("is_bot"))
            or (bot_user_id is not None and str(sender_id) == str(bot_user_id))
        )
        log.info("Business message: sender_id=%s owner_id=%s bot_id=%s sender_business_bot=%s is_bot=%s is_owner=%s", sender_id, state.get("owner_user_id"), bot_user_id, bool(sender_business_bot), sender.get("is_bot"), is_owner)
        if is_owner:
            return jsonify({"ok": True})

        if connection_id and chat_id and (business.get("text") or business.get("caption")):
            mark_business_read(connection_id, chat_id)
            if notifications_enabled and state.get("owner_user_id"):
                name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])) or sender.get("username") or str(chat_id)
                text = business.get("text") or business.get("caption") or "[медиа]"
                note = f"📩 Новое сообщение\n\n👤 {name}\n💬 {text}"
                try:
                    bot_msg(state["owner_user_id"], note, keep_last=True)
                except Exception:
                    log.exception("Failed to send notification")

            now = time.time()
            last_reply = reply_cooldowns.get(str(chat_id))
            if last_reply is not None and now - last_reply < REPLY_COOLDOWN:
                return jsonify({"ok": True})
            custom = get_custom_texts()
            reply = custom.get(str(chat_id), state["away_text"])
            try:
                business_msg(connection_id, chat_id, reply, business.get("message_id"))
                reply_cooldowns[str(chat_id)] = now
            except Exception:
                log.exception("Failed to send business reply")

    return jsonify({"ok": True})


if __name__ == "__main__":
    load_state()
    setup_webhook()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
