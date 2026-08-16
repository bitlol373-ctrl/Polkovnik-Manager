import os
import httpx

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def generate_reply(messages, instruction, model=None):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured")

    system = instruction.strip() if instruction and instruction.strip() else (
        "Отвечай от имени владельца аккаунта естественно и кратко. "
        "Не выдумывай факты, обещания, встречи или действия владельца. "
        "Если информации недостаточно, отвечай нейтрально."
    )

    payload = {
        "model": model or GROQ_MODEL,
        "messages": [{"role": "system", "content": system}] + messages,
        "temperature": 0.7,
        "max_tokens": 300,
    }

    with httpx.Client(timeout=45) as client:
        response = client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    return data["choices"][0]["message"]["content"].strip()
