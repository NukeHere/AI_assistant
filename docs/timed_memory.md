# Timed memory

The assistant can use lightweight timed memory reminders without polling the model.

## What the model sees

Every `/v1/message` prompt includes current server time in UTC and Europe/Moscow. The prompt also documents a private `assistant_memory` timer block.

Example private model directive:

```assistant_memory
{"timers":[{"summary":"помидор","full":"Вспомнить слово помидор","due_at":"2030-04-17T12:34:56Z","timezone":"Europe/Moscow"}]}
```

The server strips this block from the user-visible answer and stores it in `timed_memories`.

## What happens when time arrives

The server checks due timers in a lightweight background loop (every 5 seconds by default) and also during message/history/sync activity. Due timers are marked triggered and materialized as system messages in the conversation event stream. No model request or tokens are needed while waiting.

If the app client is linked to one or more authorized Telegram users, each due timer is also sent to them as a private Telegram message. Configure the interval with `TIMED_MEMORY_POLL_SECONDS`; set it to `0` to disable the background loop.

Render free services can be suspended while inactive. Code inside a suspended service cannot wake itself, so exact-time reminders require an always-awake service or an external scheduler that periodically wakes the web service.

## Commands

- `/timers`, `/reminders`, `таймеры`, `напоминания` show active scheduled timed memories.
- Normal memory commands still work: `/memory`, `/remember ...`, `запомни: ...`.

## Backups

Snapshots include scheduled and triggered timed memories, so local backup-agent copies preserve model reminders.
