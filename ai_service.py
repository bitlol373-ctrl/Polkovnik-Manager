import os
import logging
import re
import time
from pathlib import Path
from groq import Groq
from groq import RateLimitError

log = logging.getLogger("polkovnik-manager.ai")

DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
DEFAULT_CONTEXT = max(2, int(os.environ.get("AI_CONTEXT_MESSAGES", "6")))
_RATE_LIMIT_UNTIL = 0.0
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
    return os.environ.get("AI_BASE_PROMPT", "").strip() or _load_file_prompt()


def build_prompt(personal_prompt=None):
    base = get_base_prompt()
    personal = (personal_prompt or "").strip()
    if not personal:
        return base
    return base + "\n\nДополнительные правила именно для этого собеседника:\n" + personal


def _retry_after_seconds(exc):
    text = str(exc)
    match = re.search(r"try again in ([0-9]+(?:\.[0-9]+)?)s", text, re.I)
    if match:
        return max(30, int(float(match.group(1))) + 2)
    match = re.search(r"try again in ([0-9]+)m", text, re.I)
    if match:
        return max(30, int(match.group(1)) * 60 + 2)
    return 60


def rate_limit_remaining():
    return max(0, int(_RATE_LIMIT_UNTIL - time.time()))


def _looks_like_foreign_garbage(text):
    # Keep ordinary URLs/emails aside; detect CJK/Korean/Arabic characters in a response.
    return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af\u0600-\u06ff]", text))


def generate_reply(message_text, history=None, system_prompt=None, personal_prompt=None):
    global _RATE_LIMIT_UNTIL
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return None

    remaining = rate_limit_remaining()
    if remaining:
        log.warning("Groq rate limit cooldown active: %ss remaining", remaining)
        return None

    client = Groq(api_key=key)
    prompt = system_prompt or build_prompt(personal_prompt)
    context_limit = DEFAULT_CONTEXT
    trimmed_history = (history or [])[-context_limit:]
    messages = [{"role": "system", "content": prompt}]
    messages.extend(trimmed_history)
    messages.append({"role": "user", "content": message_text})

    try:
        log.info("Generating AI reply: model=%s prompt_chars=%s context=%s", os.environ.get("GROQ_MODEL", DEFAULT_MODEL), len(prompt), len(trimmed_history))
        response = client.chat.completions.create(
            model=os.environ.get("GROQ_MODEL", DEFAULT_MODEL),
            messages=messages,
            temperature=0.85,
            max_tokens=220,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            return None

        if _looks_like_foreign_garbage(text):
            log.warning("Rejected AI response containing unexpected non-Latin script: %r", text)
            return None

        return text
    except RateLimitError as exc:
        wait = _retry_after_seconds(exc)
        _RATE_LIMIT_UNTIL = time.time() + wait
        log.warning("Groq rate limit reached; cooldown set for %ss", wait)
        return None
    except Exception:
        log.exception("Groq request failed")
        return None
