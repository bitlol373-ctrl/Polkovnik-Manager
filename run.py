import logging
from flask import request

import main as manager
from ai_service import ai_enabled, generate_reply

log = logging.getLogger("polkovnik-manager.ai")
_ai_messages = {}
_current_business = {}


def _remember_business():
    update = request.get_json(silent=True) or {}
    business = update.get("business_message")
    if business:
        chat_id = (business.get("chat") or {}).get("id")
        if chat_id is not None:
            _current_business[str(chat_id)] = business


manager.app.before_request(_remember_business)
_original_business_msg = manager.business_msg


def ai_business_msg(connection_id, chat_id, text, reply_to=None):
    if not ai_enabled():
        return _original_business_msg(connection_id, chat_id, text, reply_to)

    business = _current_business.get(str(chat_id), {})
    incoming = (business.get("text") or business.get("caption") or "").strip()

    # Пока ИИ работает с текстом и подписями. Голосовые/кружки оставляем
    # на обычном ответе; распознавание добавим следующим этапом.
    if not incoming:
        return _original_business_msg(connection_id, chat_id, text, reply_to)

    history = _ai_messages.setdefault(str(chat_id), [])
    reply = generate_reply(incoming, history=history)
    if not reply:
        return _original_business_msg(connection_id, chat_id, text, reply_to)

    history.append({"role": "user", "content": incoming})
    history.append({"role": "assistant", "content": reply})
    if len(history) > 20:
        del history[:-20]

    log.info("AI reply generated: chat_id=%s", chat_id)
    return _original_business_msg(connection_id, chat_id, reply, reply_to)


manager.business_msg = ai_business_msg

if __name__ == "__main__":
    manager.load_state()
    manager.setup_webhook()
    manager.app.run(host="0.0.0.0", port=int(__import__("os").environ.get("PORT", "10000")))
