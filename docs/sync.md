# Multi-device sync

The assistant now treats chat history as an append-only event stream instead of a single client-owned text log.

## Why

Desktop and Android can both create messages. A sync operation must merge those messages and never delete local history just because another device has not seen it yet.

## Endpoint

`POST /v1/sync`

Request:

```json
{
  "client_id": "primary-user",
  "conversation_id": "default",
  "device_id": "desktop-main",
  "limit": 400,
  "messages": [
    {
      "message_id": "desktop-main-uuid",
      "role": "user",
      "content": "Привет",
      "created_at": "2026-09-11T08:00:00Z",
      "client_created_at": "2026-09-11T08:00:00Z",
      "device_id": "desktop-main"
    }
  ]
}
```

Response returns merged `messages`, active `persona`, imported count, and conversation `state`.

## Merge rules

- `message_id` is the strongest identity.
- If no id exists, the server derives one from conversation, role, content hash, and client timestamp.
- Sync is append-only. Missing remote messages do not delete local messages.
- Snapshot import uses the same de-duplication rules.
- Clients keep system/status lines local; only user/assistant messages are synced.

## UI rendering

Model text is still returned as `text` for compatibility, but `/v1/message` also returns `render_blocks`. Clients may render structured lines such as `Observation`, `Diagnosis`, `Command action`, and `Conclusion` as immersive UI blocks without showing raw Markdown markers.
