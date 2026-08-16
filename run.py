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
        combined = base.strip()
        if personal.strip():
            combined += "\n\nДополнительные правила именно для этого собеседника:\n" + personal.strip()
    return (combined.replace("{name}", name).replace("{username}", username).replace("{chat_id}", str(chat_id)))


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


def ai_main_menu():
    base = get_base_settings()
    return {"inline_keyboard": [
        [{"text": "🌐 Базовый промпт", "callback_data": "ai:base"}],
        [{"text": "👤 Персональный промпт", "callback_data": "ai:personal"}],
        [{"text": "🟢 ИИ включён" if base["enabled"] else "🔴 ИИ выключен", "callback_data": "ai:toggle:0"}],
        [{"text": f"💬 Контекст: {base['context_size']} сообщений", "callback_data": "ai:context:0"}],
        [{"text": "⬅️ Назад", "callback_data": "back"}],
    ]}


def ai_prompt_menu(target_chat, title):
    s = get_base_settings() if target_chat == 0 else get_ai_settings(target_chat)
    prompt = s["prompt"] or ("Используется встроенный базовый промпт." if target_chat == 0 else "Не задан — используется базовый промпт.")
    if len(prompt) > 350:
        prompt = prompt[:350] + "…"
    rows = [[{"text": "✏️ Изменить промпт", "callback_data": f"ai:prompt:{target_chat}"}]]
    if target_chat != 0:
        rows.append([{"text": "🧹 Сбросить → базовый", "callback_data": f"ai:reset:{target_chat}"}])
    rows.append([{"text": "⬅️ Назад к ИИ", "callback_data": "ai:menu:0"}])
    return f"{title}\n\nПромпт:\n{prompt}", {"inline_keyboard": rows}


def _enhanced_menu(original):
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
            manager.bot_msg(msg["chat"]["id"], "🧠 ИИ-настройки\n\nВыбери, что хочешь настроить:", ai_main_menu(), keep_last=True)
            return jsonify({"ok": True})

        pending = _pending_ai_prompt.get(str(sender_id))
        if pending is not None:
            ok, error = save_ai_settings(pending, prompt=text)
            _pending_ai_prompt.pop(str(sender_id), None)
            if ok:
                manager.bot_msg(msg["chat"]["id"], "✅ Промпт сохранён.", ai_main_menu() if pending == 0 else ai_prompt_menu(pending, "👤 Персональный промпт")[1], keep_last=True)
            else:
                manager.bot_msg(msg["chat"]["id"], f"❌ Не удалось сохранить промпт:\n{error}", keep_last=True)
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
        try:
            manager.tg("answerCallbackQuery", {"callback_query_id": cb["id"]})
        except Exception as e:
            log.warning("AI callback answer failed: %s", e)

        if action == "menu":
            manager.bot_msg(cb_chat, "🧠 ИИ-настройки\n\nВыбери, что хочешь настроить:", ai_main_menu(), keep_last=True)
        elif action == "base":
            text, keyboard = ai_prompt_menu(0, "🌐 Базовый промпт для всех")
            manager.bot_msg(cb_chat, text, keyboard, keep_last=True)
        elif action == "personal":
            manager.bot_msg(cb_chat, "👤 Персональный промпт\n\nЧтобы настроить персональный промпт, сначала выбери собеседника из списка последних чатов.", {"inline_keyboard": [
                [{"text": "➕ Выбрать из последних чатов", "callback_data": "ai:chatlist"}],
                [{"text": "⬅️ Назад к ИИ", "callback_data": "ai:menu:0"}],
            ]}, keep_last=True)
        elif action == "chatlist":
            chats = []
            for cid, msg_data in _current_business.items():
                sender = msg_data.get("from") or {}
                name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])) or sender.get("username") or f"Чат {cid}"
                chats.append((cid, name))
            if not chats:
                manager.bot_msg(cb_chat, "Пока нет сохранённых входящих чатов. Дождись сообщения от человека и открой меню снова.", {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "ai:menu:0"}]]}, keep_last=True)
            else:
                rows = [[{"text": f"👤 {name}", "callback_data": f"ai:personalchat:{cid}"}] for cid, name in chats[-20:]]
                rows.append([{"text": "⬅️ Назад", "callback_data": "ai:menu:0"}])
                manager.bot_msg(cb_chat, "👤 Выбери собеседника:", {"inline_keyboard": rows}, keep_last=True)
        elif action == "personalchat":
            target = int(parts[2])
            text, keyboard = ai_prompt_menu(target, "👤 Персональный промпт")
            manager.bot_msg(cb_chat, text, keyboard, keep_last=True)
        elif action == "toggle":
            target = int(parts[2])
            s = get_base_settings() if target == 0 else get_ai_settings(target)
            ok, error = save_ai_settings(target, enabled=not s["enabled"])
            manager.bot_msg(cb_chat, "✅ Статус изменён." if ok else f"❌ {error}", ai_main_menu(), keep_last=True)
        elif action == "prompt":
            target = int(parts[2])
            _pending_ai_prompt[str(cb_sender)] = target
            label = "базовый промпт для всех чатов" if target == 0 else "персональный промпт для выбранного чата"
            manager.bot_msg(cb_chat, f"✏️ Отправь следующим сообщением {label}.\n\nПеременные: {{name}}, {{username}}, {{chat_id}}", keep_last=True)
        elif action == "reset":
            target = int(parts[2])
            ok, error = save_ai_settings(target, prompt="")
            manager.bot_msg(cb_chat, "✅ Персональный промпт сброшен. Теперь используется базовый." if ok else f"❌ {error}", ai_main_menu(), keep_last=True)
        elif action == "context":
            target = int(parts[2])
            s = get_base_settings() if target == 0 else get_ai_settings(target)
            options = [5, 10, 20, 30]
            current = int(s["context_size"])
            nxt = options[(options.index(current) + 1) % len(options)] if current in options else 10
            save_ai_settings(target, context_size=nxt)
            _ai_messages.pop(str(target), None)
            manager.bot_msg(cb_chat, f"💬 Контекст: {nxt} сообщений", ai_main_menu(), keep_last=True)
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
    return _original_business_msg(connection_id, chat_id, reply, reply_to)

manager.business_msg = ai_business_msg

if __name__ == "__main__":
    manager.load_state()
    manager.setup_webhook()
    manager.app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
