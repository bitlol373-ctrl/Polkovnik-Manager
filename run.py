import logging
import os
from flask import request, jsonify

import main as manager
from ai_service import ai_enabled, generate_reply, build_prompt

log = logging.getLogger("polkovnik-manager.ai")
_ai_messages = {}
_current_business = {}
_pending_ai_prompt = {}


def _ai_table():
    return getattr(manager, "supabase", None)


def get_ai_settings(chat_id):
    db = _ai_table()
    defaults = {"enabled": True, "prompt": "", "context_size": 10}
    if not db:
        return defaults
    try:
        row = db.table("ai_chat_settings").select("enabled,prompt,context_size").eq("chat_id", int(chat_id)).maybe_single().execute().data
        if row:
            defaults.update({k: row[k] for k in defaults if row.get(k) is not None})
    except Exception as e:
        log.warning("AI settings read failed for %s: %s", chat_id, e)
    return defaults


def get_base_settings():
    return get_ai_settings(0)


def get_effective_prompt(chat_id, business):
    base = get_base_settings().get("prompt", "")
    personal = get_ai_settings(chat_id).get("prompt", "")
    sender = business.get("from") or {}
    name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])) or sender.get("username") or "собеседник"
    username = sender.get("username") or ""
    combined = build_prompt(personal_prompt=personal or None)
    if base.strip():
        # The database base prompt replaces the built-in default while keeping any per-chat override.
        combined = base.strip()
        if personal.strip():
            combined += "\n\nДополнительные правила именно для этого собеседника:\n" + personal.strip()
    return (combined
            .replace("{name}", name)
            .replace("{username}", username)
            .replace("{chat_id}", str(chat_id)))


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


def ai_menu(chat_id=None):
    if chat_id in (None, 0):
        s = get_base_settings()
        title = "🌐 Базовые настройки ИИ"
        target = 0
    else:
        s = get_ai_settings(chat_id)
        title = f"👤 Настройки чата {chat_id}"
        target = chat_id
    return {"inline_keyboard": [
        [{"text": "🟢 ИИ включён" if s["enabled"] else "🔴 ИИ выключен", "callback_data": f"ai:toggle:{target}"}],
        [{"text": "✏️ Изменить промпт", "callback_data": f"ai:prompt:{target}"}],
        [{"text": f"💬 Контекст: {s['context_size']} сообщений", "callback_data": f"ai:context:{target}"}],
        *([] if target == 0 else [[{"text": "🧹 Сбросить персональный промпт", "callback_data": f"ai:reset:{target}"}]]),
        [{"text": "👤 Переменные чата", "callback_data": f"ai:vars:{target}"}],
        [{"text": "⬅️ Назад", "callback_data": "back"}],
    ]}


def ai_status_text(chat_id=None):
    if chat_id in (None, 0):
        s = get_base_settings()
        title = "🌐 Базовые настройки ИИ"
        prompt = s["prompt"] or "Используется встроенный базовый промпт."
    else:
        s = get_ai_settings(chat_id)
        title = f"👤 Настройки чата {chat_id}"
        prompt = s["prompt"] or "Не задан — используется базовый промпт."
    if len(prompt) > 350:
        prompt = prompt[:350] + "…"
    return f"{title}\n\nСтатус: {'🟢 включён' if s['enabled'] else '🔴 выключен'}\nКонтекст: {s['context_size']} сообщений\n\nПромпт:\n{prompt}"


def _enhanced_menu(original, chat_id=None):
    m = original()
    rows = m.get("inline_keyboard", [])
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

    msg = update.get("message") or {}
    sender_id = (msg.get("from") or {}).get("id")
    owner_id = manager.state.get("owner_user_id")
    text = (msg.get("text") or "").strip()
    if msg and text and str(sender_id) == str(owner_id):
        if text == "/ai":
            manager.bot_msg(msg["chat"]["id"], "🧠 ИИ-настройки\n\nВыбери, что хочешь настроить:", {"inline_keyboard": [
                [{"text": "🌐 Базовый промпт для всех", "callback_data": "ai:menu:0"}],
                [{"text": "👤 Настроить конкретный чат", "callback_data": "ai:select"}],
            ]}, keep_last=True)
            return jsonify({"ok": True})
        if text.startswith("/ai "):
            target = text[4:].strip()
            if target.lstrip("-").isdigit():
                target = int(target)
                manager.bot_msg(msg["chat"]["id"], ai_status_text(target), ai_menu(target), keep_last=True)
            else:
                manager.bot_msg(msg["chat"]["id"], "❌ Формат: /ai CHAT_ID", keep_last=True)
            return jsonify({"ok": True})

        pending = _pending_ai_prompt.get(str(sender_id))
        if pending is not None:
            ok, error = save_ai_settings(pending, prompt=text)
            _pending_ai_prompt.pop(str(sender_id), None)
            manager.bot_msg(msg["chat"]["id"], "✅ Промпт сохранён." if ok else f"❌ Не удалось сохранить промпт:\n{error}", ai_menu(pending), keep_last=True)
            return jsonify({"ok": True})

    cb = update.get("callback_query") or {}
    data = cb.get("data", "")
    cb_msg = cb.get("message") or {}
    cb_chat = (cb_msg.get("chat") or {}).get("id")
    cb_sender = (cb.get("from") or {}).get("id")
    owner_id = manager.state.get("owner_user_id")
    if cb and str(cb_sender) == str(owner_id) and cb_chat and data.startswith("ai:"):
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        if action == "select":
            manager.bot_msg(cb_chat, "👤 Для настройки конкретного чата отправь:\n/ai CHAT_ID\n\nID можно взять из уведомления о новом сообщении.", keep_last=True)
            return jsonify({"ok": True})
        try:
            target_chat = int(parts[2]) if len(parts) > 2 else int(cb_chat)
        except (ValueError, TypeError):
            target_chat = int(cb_chat)
        try:
            manager.tg("answerCallbackQuery", {"callback_query_id": cb["id"]})
        except Exception as e:
            log.warning("AI callback answer failed: %s", e)
        if action == "menu":
            manager.bot_msg(cb_chat, ai_status_text(target_chat), ai_menu(target_chat), keep_last=True)
        elif action == "toggle":
            s = get_base_settings() if target_chat == 0 else get_ai_settings(target_chat)
            ok, error = save_ai_settings(target_chat, enabled=not s["enabled"])
            manager.bot_msg(cb_chat, ai_status_text(target_chat) if ok else f"❌ {error}", ai_menu(target_chat), keep_last=True)
        elif action == "prompt":
            _pending_ai_prompt[str(cb_sender)] = target_chat
            label = "базовый промпт для всех чатов" if target_chat == 0 else f"персональный промпт для чата {target_chat}"
            manager.bot_msg(cb_chat, f"✏️ Отправь следующим сообщением новый {label}.\n\nПеременные: {{name}}, {{username}}, {{chat_id}}", ai_menu(target_chat), keep_last=True)
        elif action == "reset":
            ok, error = save_ai_settings(target_chat, prompt="")
            manager.bot_msg(cb_chat, "✅ Персональный промпт сброшен. Теперь используется базовый." if ok else f"❌ {error}", ai_menu(target_chat), keep_last=True)
        elif action == "context":
            s = get_base_settings() if target_chat == 0 else get_ai_settings(target_chat)
            current = int(s["context_size"])
            options = [5, 10, 20, 30]
            nxt = options[(options.index(current) + 1) % len(options)] if current in options else 10
            save_ai_settings(target_chat, context_size=nxt)
            _ai_messages.pop(str(target_chat), None)
            manager.bot_msg(cb_chat, ai_status_text(target_chat), ai_menu(target_chat), keep_last=True)
        elif action == "vars":
            manager.bot_msg(cb_chat, "👤 Переменные\n\n{name} — имя собеседника\n{username} — username\n{chat_id} — ID чата\n\nПример:\nОбщайся с {name} максимально неформально.", ai_menu(target_chat), keep_last=True)
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

    history = _ai_messages.setdefault(str(chat_id), [])
    limit = max(2, int(settings["context_size"])) * 2
    history = history[-limit:]
    prompt = get_effective_prompt(chat_id, business)
    reply = generate_reply(incoming, history=history, system_prompt=prompt)
    if not reply:
        return _original_business_msg(connection_id, chat_id, text, reply_to)

    history.append({"role": "user", "content": incoming})
    history.append({"role": "assistant", "content": reply})
    _ai_messages[str(chat_id)] = history[-limit:]
    log.info("AI reply generated: chat_id=%s prompt_len=%s personal=%s", chat_id, len(prompt), bool(settings.get("prompt")))
    return _original_business_msg(connection_id, chat_id, reply, reply_to)


manager.business_msg = ai_business_msg

if __name__ == "__main__":
    manager.load_state()
    manager.setup_webhook()
    manager.app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
