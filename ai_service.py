import os
import logging
from pathlib import Path
from groq import Groq

log = logging.getLogger("polkovnik-manager.ai")

DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
_FALLBACK_PROMPT = """Ты — AI-ассистент, который помогает вести обычную личную переписку.\nОтвечай естественно, живо и кратко, без канцелярита, морализаторства и шаблонных фраз.\nПо умолчанию отвечай на русском языке и не вставляй случайные слова из других языков.\nУчитывай предыдущий контекст и не выдумывай факты."""


def _load_file_prompt():
    path = Path(__file__).resolve().parent / "prompts" / "base.txt"
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    except Exception:
        log.exception("Failed to load prompts/base.txt")
    return _FALLBACK_PROMPT


def ai_enabled():
    return bool(os.environ.get("GROQ_API_KEY", "").strip()) and os.environ.get("AI_ENABLED", "1").lower() not in {"0", "false", "no", "off"}


def get_base_prompt():
    # AI_BASE_PROMPT remains available as an environment-variable override.
    return os.environ.get("AI_BASE_PROMPT", "").strip() or _load_file_prompt()


def build_prompt(personal_prompt=None):
    base = get_base_prompt()
    personal = (personal_prompt or "").strip()
    if not personal:
        return base
    return base + "\n\nДополнительные правила именно для этого собеседника:\n" + personal


def generate_reply(message_text, history=None, system_prompt=None, personal_prompt=None):
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return None

    client = Groq(api_key=key)
    prompt = system_prompt or build_prompt(personal_prompt)
    messages = [{"role": "system", "content": prompt}]
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": message_text})

    try:
        log.info("Generating AI reply: model=%s prompt_len=%s history=%s personal=%s", os.environ.get("GROQ_MODEL", DEFAULT_MODEL), len(prompt), len(history or []), bool(personal_prompt))
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
