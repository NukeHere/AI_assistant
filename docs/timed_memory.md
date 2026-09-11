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

On message/history/sync activity the server checks due timers. Due timers are marked triggered and materialized as system messages in the conversation event stream. This avoids spending tokens while waiting.

## Commands

- `/timers`, `/reminders`, `таймеры`, `напоминания` show active scheduled timed memories.
- Normal memory commands still work: `/memory`, `/remember ...`, `запомни: ...`.

## Backups

Snapshots include scheduled and triggered timed memories, so local backup-agent copies preserve model reminders.
