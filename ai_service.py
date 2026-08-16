import os
import logging
from groq import Groq

log = logging.getLogger("polkovnik-manager.ai")

DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
DEFAULT_PROMPT = os.environ.get(
    "AI_SYSTEM_PROMPT",
    """Ты — AI-ассистент, который помогает вести обычную личную переписку.
Твоя задача — писать естественно, живо и кратко, как реальный человек.
Не используй канцелярит, шаблонные фразы, морализаторство или лишние предупреждения.
Не комментируй стиль речи собеседника и не проси его воздерживаться от мата или сленга.
Если собеседник использует мат или разговорный язык, можешь спокойно использовать такой же уровень неформальности, если это соответствует контексту.
Не начинай ответы с «Конечно», «Разумеется», «Понимаю» и подобных шаблонных вступлений.
Не выдумывай факты, встречи, обещания или действия от имени владельца.
Если вопрос требует решения, которого у тебя нет, коротко скажи, что владелец ответит позже.
Не упоминай, что ты ИИ или бот, если это не требуется контекстом.""",
)


def ai_enabled():
    return bool(os.environ.get("GROQ_API_KEY", "").strip()) and os.environ.get("AI_ENABLED", "1").lower() not in {"0", "false", "no", "off"}


def generate_reply(message_text, history=None, system_prompt=None):
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return None

    client = Groq(api_key=key)
    prompt = system_prompt or DEFAULT_PROMPT
    messages = [{"role": "system", "content": prompt}]
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": message_text})

    try:
        log.info("Generating AI reply: model=%s prompt_len=%s history=%s", os.environ.get("GROQ_MODEL", DEFAULT_MODEL), len(prompt), len(history or []))
        response = client.chat.completions.create(
            model=os.environ.get("GROQ_MODEL", DEFAULT_MODEL),
            messages=messages,
            temperature=0.85,
            max_tokens=300,
        )
        text = (response.choices[0].message.content or "").strip()
        return text or None
    except Exception:
        log.exception("Groq request failed")
        return None
