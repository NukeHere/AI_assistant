# Telegram interface

Telegram is an extra communication channel for the same AI Assistant core. It does not replace the desktop or Android app; it sends messages into the same server, memory, persona and sync layer.

## Environment variables

Set these on Render:

- `TG_BOT_API_KEY` — Telegram bot token from BotFather. Keep it secret.
- `TG_WEBHOOK_SECRET` — optional but recommended secret for Telegram webhook requests.
- `TG_BOT_USERNAME` — bot username without `@`, useful for group mentions. Default in code: `VBDsThirdSon_bot`.
- `TG_GUEST_CHAT_MODE=true` — allow unlinked users to talk in guest mode.
- `TG_SECRETARY_MODE=true` — ignore group noise unless the message is a command or mentions the bot.
- `TG_BOT_TO_BOT=true` — allow bot-to-bot messages if you intentionally need them.

## Webhook URL

The endpoint is:

```text
https://ai-assistant-4yn0.onrender.com/v1/telegram/webhook
```

Register it with Telegram after `TG_BOT_API_KEY` is set. If `TG_WEBHOOK_SECRET` is used, pass the same value as Telegram `secret_token`.

## User flow

1. In desktop or Android, send `/id`.
2. The assistant returns the app `client_id`.
3. In Telegram, send `/link <client_id>`.
4. After that, Telegram private messages use the same app client and default conversation, so they sync with desktop and Android.

Before linking, each Telegram user gets a stable guest client id like `tg-guest-...`. Guest memory is capped by normal prompt retrieval and can be migrated into the linked app profile during `/link`.

## Commands

- `/start` — explains guest mode and linking.
- `/link <client_id>` — links Telegram to the app profile.
- `/unlink` — returns Telegram to guest mode.
- `/whoami` or `/id` — shows Telegram id, mode and active app client id.

## MVP secretary behavior

For token economy, the first implementation uses cheap rules instead of an extra LLM classifier:

- private messages are processed;
- commands are processed;
- plain guest group chatter is ignored;
- direct group mentions such as `@VBDsThirdSon_bot`, `бот`, `секретарь`, `ANA`, `ALIEN` are processed for both guest and authorized users;
- authorized group messages without a direct mention are ignored as noise.

Later this can be replaced by a tiny classifier prompt or local heuristic+embedding scorer.
## Audit logs

The server writes compact JSONL audit records without API tokens or webhook secrets. By default the file is `./data/assistant-audit.jsonl`; override it with `ASSISTANT_AUDIT_LOG_PATH`.

Protected log tail endpoint:

```text
POST /v1/logs
Authorization: Bearer <APP_API_TOKEN>
{"limit":100}
```

Use this to see incoming desktop, Android and Telegram messages, routing decisions, ignored group messages, memory save/recall counters and Telegram response route.

Telegram authorization links are included in `/v1/snapshot` as `telegram_users`, so the PC backup agent can preserve `telegram_user_id ↔ app_client_id ↔ username` across Render redeploys.