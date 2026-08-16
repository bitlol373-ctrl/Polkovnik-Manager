import os
import logging
from groq import Groq

log = logging.getLogger("polkovnik-manager.ai")

DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
DEFAULT_PROMPT = os.environ.get(
    "AI_SYSTEM_PROMPT",
    """Ты — помощник владельца Telegram Business. Отвечай естественно и коротко, как обычный человек в переписке. Не выдумывай факты, встречи, обещания или действия от имени владельца. Если вопрос требует решения владельца, вежливо скажи, что он ответит позже. Не упоминай, что ты ИИ или бот, если это не требуется контекстом.""",
)


def ai_enabled():
    return bool(os.environ.get("GROQ_API_KEY", "").strip()) and os.environ.get("AI_ENABLED", "1").lower() not in {"0", "false", "no", "off"}


def generate_reply(message_text, history=None, system_prompt=None):
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return None

    client = Groq(api_key=key)
    messages = [{"role": "system", "content": system_prompt or DEFAULT_PROMPT}]
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": message_text})

    try:
        response = client.chat.completions.create(
            model=os.environ.get("GROQ_MODEL", DEFAULT_MODEL),
            messages=messages,
            temperature=0.75,
            max_tokens=300,
        )
        text = (response.choices[0].message.content or "").strip()
        return text or None
    except Exception:
        log.exception("Groq request failed")
        return None
