# Polkovnik Manager

Telegram Secretary/Chat Automation backend.

## Current stage

The server receives Telegram `business_message` updates and can send a test reply through the connected account using `business_connection_id`.

## Environment variables

- `TELEGRAM_BOT_TOKEN` — token from BotFather. Keep private.
- `WEBHOOK_SECRET` — random secret used to authenticate Telegram webhook requests.
- `PUBLIC_URL` — public HTTPS URL of the deployed service, without a trailing slash.

## Local run

```bash
pip install -r requirements.txt
python main.py
```

The server listens on `PORT` (default `8080`).

## Next stage

Replace the temporary test reply with an AI provider, add per-chat memory, allow/deny lists, pause/resume controls, and a human-approval mode.
