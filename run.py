import logging
import os
from flask import request, jsonify

import main as manager
from ai_service import ai_enabled, generate_reply

log = logging.getLogger("polkovnik-manager.ai")
_ai_messages = {}
_current_business = {}
_pending_ai_prompt = {}


def _ai_table():
    return getattr(manager, "supabase", None)


def get_ai_settings(chat_id):
    db = _ai_table()
    defaults = {"enabled": True, "prompt": "Отвечай естественно, как обычный человек. Не используй канцелярит, не начинай ответы с «Конечно» и не повторяй шаблонные фразы.", "context_size": 10}
    if not db:
        return defaults
    try:
        row = db.table("ai_chat_settings").select("enabled,prompt,context_size").eq("chat_id", int(chat_id)).maybe_single().execute().data
        if row:
            defaults.update({k: row[k] for k in defaults if row.get(k) is not None})
    except Exception as e:
        log.warning("AI settings read failed: %s", e)
    return defaults


def save_ai_settings(chat_id, **values):
    db = _ai_table()
    if not db:
        return False, "Supabase не подключён"
    try:
        payload = {"chat_id": int(chat_id), **values}
        db.table("ai_chat_settings").upsert(payload).execute()
        return True, None
    except Exception as e:
        return False, str(e)


def ai_menu(chat_id):
    s = get_ai_settings(chat_id)
    return {"inline_keyboard": [
        [{"text": "🟢 ИИ включён" if s["enabled"] else "🔴 ИИ выключен", "callback_data": "ai:toggle"}],
        [{"text": "✏️ Изменить промпт", "callback_data": "ai:prompt"}],
        [{"text": f"💬 Контекст: {s['context_size']} сообщений", "callback_data": "ai:context"}],
        [{"text": "👤 Переменные чата", "callback_data": "ai:vars"}],
        [{"text": "⬅️ Назад", "callback_data": "back"}],
    ]}


def ai_status_text(chat_id):
    s = get_ai_settings(chat_id)
    prompt = s["prompt"]
    if len(prompt) > 350:
        prompt = prompt[:350] + "…"
    return f"🧠 ИИ-настройки\n\nСтатус: {'🟢 включён' if s['enabled'] else '🔴 выключен'}\nКонтекст: {s['context_size']} сообщений\n\nПромпт:\n{prompt}"


def _enhanced_menu(original, chat_id=None):
    m = original()
    rows = m.get("inline_keyboard", [])
    # Keep the existing stable controls and add AI as the last control.
    if not any(any(x.get("callback_data") == "ai:menu" for x in row) for row in rows):
        rows.append([{"text": "🧠 ИИ-ассистент", "callback_data": "ai:menu"}])
    return {"inline_keyboard": rows}


_original_menu = manager.menu
manager.menu = lambda: _enhanced_menu(_original_menu)


def _remember_business():
    update = request.get_json(silent=True) or {}
    business = update.get("business_message")
    if business:
        chat_id = (business.get("chat") or {}).get("id")
        if chat_id is not None:
            _current_business[str(chat_id)] = business

    # Handle AI control messages before main.py consumes owner messages.
    msg = update.get("message") or {}
    sender_id = (msg.get("from") or {}).get("id")
    owner_id = manager.state.get("owner_user_id")
    text = (msg.get("text") or "").strip()
    if msg and text and str(sender_id) == str(owner_id):
        if text == "/ai":
            manager.bot_msg(msg["chat"]["id"], ai_status_text(msg["chat"]["id"]), ai_menu(msg["chat"]["id"]), keep_last=True)
            return jsonify({"ok": True})
        pending = _pending_ai_prompt.get(str(sender_id))
        if pending:
            ok, error = save_ai_settings(pending, prompt=text)
            _pending_ai_prompt.pop(str(sender_id), None)
            manager.bot_msg(msg["chat"]["id"], "✅ Промпт сохранён." if ok else f"❌ Не удалось сохранить промпт:\n{error}", ai_menu(pending), keep_last=True)
            return jsonify({"ok": True})

    cb = update.get("callback_query") or {}
    data = cb.get("data", "")
    cb_msg = cb.get("message") or {}
    cb_chat = (cb_msg.get("chat") or {}).get("id")
    cb_sender = (cb.get("from") or {}).get("id")
    if cb and str(cb_sender) == str(owner_id) and cb_chat and data.startswith("ai:"):
        try:
            manager.tg("answerCallbackQuery", {"callback_query_id": cb["id"]})
        except Exception as e:
            log.warning("AI callback answer failed: %s", e)
        if data == "ai:menu":
            manager.bot_msg(cb_chat, ai_status_text(cb_chat), ai_menu(cb_chat), keep_last=True)
        elif data == "ai:toggle":
            s = get_ai_settings(cb_chat); ok, error = save_ai_settings(cb_chat, enabled=not s["enabled"])
            manager.bot_msg(cb_chat, ai_status_text(cb_chat) if ok else f"❌ {error}", ai_menu(cb_chat), keep_last=True)
        elif data == "ai:prompt":
            _pending_ai_prompt[str(cb_sender)] = cb_chat
            manager.bot_msg(cb_chat, "✏️ Отправь следующим сообщением новый системный промпт для этого чата.\n\nПример:\nОтвечай естественно, коротко, с юмором и без официоза.", ai_menu(cb_chat), keep_last=True)
        elif data == "ai:context":
            s = get_ai_settings(cb_chat)
            current = int(s["context_size"])
            options = [5, 10, 20, 30]
            nxt = options[(options.index(current) + 1) % len(options)] if current in options else 10
            save_ai_settings(cb_chat, context_size=nxt)
            _ai_messages.pop(str(cb_chat), None)
            manager.bot_msg(cb_chat, ai_status_text(cb_chat), ai_menu(cb_chat), keep_last=True)
        elif data == "ai:vars":
            manager.bot_msg(cb_chat, "👤 Переменные для промпта\n\n{name} — имя собеседника\n{username} — username\n{chat_id} — ID чата\n\nПример:\n«Общайся с {name} максимально неформально».\n\nПеременные будут подставляться перед отправкой запроса в ИИ.", ai_menu(cb_chat), keep_last=True)
        return jsonify({"ok": True})


manager.app.before_request(_remember_business)
_original_business_msg = manager.business_msg


def ai_business_msg(connection_id, chat_id, text, reply_to=None):
    if not ai_enabled():
        return _original_business_msg(connection_id, chat_id, text, reply_to)

    business = _current_business.get(str(chat_id), {})
    incoming = (business.get("text") or business.get("caption") or "").strip()
    if not incoming:
        return _original_business_msg(connection_id, chat_id, text, reply_to)

    settings = get_ai_settings(chat_id)
    if not settings["enabled"]:
        return _original_business_msg(connection_id, chat_id, text, reply_to)

    sender = business.get("from") or {}
    name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])) or sender.get("username") or "собеседник"
    username = sender.get("username") or ""
    prompt = settings["prompt"].replace("{name}", name).replace("{username}", username).replace("{chat_id}", str(chat_id))

    history = _ai_messages.setdefault(str(chat_id), [])
    limit = max(2, int(settings["context_size"])) * 2
    history = history[-limit:]
    reply = generate_reply(incoming, history=history, system_prompt=prompt)
    if not reply:
        return _original_business_msg(connection_id, chat_id, text, reply_to)

    history.append({"role": "user", "content": incoming})
    history.append({"role": "assistant", "content": reply})
    _ai_messages[str(chat_id)] = history[-limit:]
    log.info("AI reply generated: chat_id=%s", chat_id)
    return _original_business_msg(connection_id, chat_id, reply, reply_to)


manager.business_msg = ai_business_msg

if __name__ == "__main__":
    manager.load_state()
    manager.setup_webhook()
    manager.app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
