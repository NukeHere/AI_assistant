# Telegram interface

## Группы и временный свободный доступ

Мут сохраняет can_react_to_messages=true. При запуске сервер добавляет message_reaction
в подписку существующего webhook, если URL совпадает с TG_WEBHOOK_URL и настроен секрет.
Результат записывается в telegram_reaction_subscription_ready/failed/skipped.
Telegram присылает индивидуальные реакции только боту-администратору; анонимные реакции
не позволяют определить автора. Изменения и удаления реакций сохраняются в истории.

reply_to=ignore подавляет текст и реакцию бота в группе. mute выполняется независимо от ignore.
Сообщения супергрупп проверяются моделью даже когда обычный фильтр счёл их шумом,
чтобы не пропускать модерацию. Это увеличивает расход запросов и токенов.

Для мута по нику модель передаёт mute.username, minutes и reason. Сервер находит ID
в telegram_users, обновляемой при входящих сообщениях, и проверяет актуальный ник
через getChatMember перед ограничением. Неизвестный или устаревший ник, а также
несовпадение явно указанных ID и username не приводят к муту. Отображаемое имя не
используется как уникальный идентификатор. База соответствий подвержена тем же
ограничениям хранения SQLite на Render, что и остальная память.

Новые сообщения группы сохраняются в общей истории tg-group-<chat_id> с указанием автора.
Личная память и другие группы изолированы. Полученные сообщения, на которые бот решил
не отвечать, также сохраняются. Недоставленные Telegram сообщения сервер прочитать не может.

TG_OPEN_ACCESS=true временно даёт всем полный доступ к своему профилю. Привязки /link
не меняются; TG_OPEN_ACCESS=false возвращает прежнюю авторизацию.

Модерация: telegram_action может содержать mute с полями user_id, minutes=1, reason.
Максимальный мут временно одна минута; больший срок отклоняется кодом.
unmute принимает username или user_id и reason, снимает ограничения и объявляет результат.
group_note сохраняет краткое значимое событие в личную историю и тематическую память
текущего отправителя. Источник содержит название и ID группы, ID сообщения, время UTC.
Успешный мут/unmute автоматически сохраняется у инициатора и затронутого участника.
Эти записи доступны в личке; личные сообщения обратно в группу не переносятся.
Нужна супергруппа и право бота can_restrict_members. Права бота кешируются на минуту.
Администраторы, отсутствующие и уже ограниченные участники не мутятся.
После подтверждения Telegram бот сообщает в группе, кто замучен и на сколько минут.
Снятие ограничения по времени выполняет Telegram независимо от перезапуска сервера.
Модерация доступна при обработке сообщения моделью; существующая фильтрация шума сохраняется.

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
