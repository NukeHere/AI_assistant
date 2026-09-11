from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.embeddings import cosine_similarity, embed_text
from app.personas import Persona
from app.smart_memory import score_text


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return bool(result)


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists conversations (
                    id text primary key,
                    client_id text not null,
                    persona text not null default 'ANA',
                    alien_glossary_json text not null default '{}',
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );

                create table if not exists messages (
                    id integer primary key autoincrement,
                    conversation_id text not null,
                    role text not null,
                    content text not null,
                    created_at text not null default current_timestamp,
                    foreign key(conversation_id) references conversations(id)
                );

                create table if not exists memories (
                    id integer primary key autoincrement,
                    client_id text not null,
                    kind text not null,
                    content text not null,
                    importance integer not null default 1,
                    created_at text not null default current_timestamp
                );

                create table if not exists memory_cells (
                    id integer primary key autoincrement,
                    client_id text not null,
                    kind text not null,
                    summary text not null,
                    full_content text not null,
                    topics_json text not null default '[]',
                    embedding_json text not null default '[]',
                    importance integer not null default 1,
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp,
                    last_used_at text
                );

                create index if not exists idx_memory_cells_client_importance
                    on memory_cells (client_id, importance desc, id desc);

                create table if not exists timed_memories (
                    id integer primary key autoincrement,
                    timer_uid text unique,
                    client_id text not null,
                    conversation_id text not null,
                    summary text not null,
                    full_content text not null,
                    due_at text not null,
                    timezone text not null default 'Europe/Moscow',
                    status text not null default 'scheduled',
                    source text not null default 'model',
                    created_at text not null default current_timestamp,
                    triggered_at text,
                    materialized_message_uid text
                );

                create index if not exists idx_timed_memories_due
                    on timed_memories (client_id, conversation_id, status, due_at);

                create table if not exists telegram_users (
                    telegram_user_id text primary key,
                    app_client_id text,
                    guest_client_id text not null,
                    username text not null default '',
                    first_name text not null default '',
                    last_name text not null default '',
                    is_bot integer not null default 0,
                    is_authorized integer not null default 0,
                    created_at text not null default current_timestamp,
                    updated_at text not null default current_timestamp
                );

                create index if not exists idx_telegram_users_app_client
                    on telegram_users (app_client_id);
                """
            )
            self._ensure_column(conn, "memory_cells", "embedding_json", "text not null default '[]'")
            self._ensure_column(conn, "messages", "message_uid", "text")
            self._ensure_column(conn, "messages", "device_id", "text not null default ''")
            self._ensure_column(conn, "messages", "client_created_at", "text")
            self._ensure_column(conn, "messages", "content_hash", "text not null default ''")
            conn.execute("create unique index if not exists idx_messages_uid on messages (message_uid) where message_uid is not null")
            conn.execute("create index if not exists idx_messages_conversation_created on messages (conversation_id, created_at, id)")
            self._ensure_column(conn, "timed_memories", "timer_uid", "text")
            self._ensure_column(conn, "timed_memories", "materialized_message_uid", "text")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"pragma table_info({table})")}
        if column not in columns:
            conn.execute(f"alter table {table} add column {column} {definition}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_conversation(self, client_id: str, conversation_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "select * from conversations where id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "insert into conversations (id, client_id, persona) values (?, ?, ?)",
                    (conversation_id, client_id, Persona.ANA.value),
                )
                row = conn.execute(
                    "select * from conversations where id = ?",
                    (conversation_id,),
                ).fetchone()
            return dict(row)

    def set_persona(self, client_id: str, conversation_id: str, persona: Persona) -> None:
        self.ensure_conversation(client_id, conversation_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                update conversations
                   set persona = ?, updated_at = current_timestamp
                 where id = ?
                """,
                (persona.value, conversation_id),
            )

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        message_uid: str | None = None,
        device_id: str = "server",
        client_created_at: str | None = None,
    ) -> dict[str, Any]:
        content_hash = self.message_hash(conversation_id, role, content)
        with self._lock, self._connect() as conn:
            if message_uid:
                existing = conn.execute(
                    "select * from messages where message_uid = ? limit 1",
                    (message_uid,),
                ).fetchone()
                if existing is not None:
                    return dict(existing)
            cursor = conn.execute(
                """
                insert into messages (conversation_id, role, content, message_uid, device_id, client_created_at, content_hash)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, role, content, message_uid, device_id, client_created_at, content_hash),
            )
            row_id = int(cursor.lastrowid)
            if not message_uid:
                message_uid = f"srv-{conversation_id}-{row_id}"
                conn.execute("update messages set message_uid = ? where id = ?", (message_uid, row_id))
            conn.execute(
                "update conversations set updated_at = current_timestamp where id = ?",
                (conversation_id,),
            )
            row = conn.execute("select * from messages where id = ?", (row_id,)).fetchone()
        return dict(row)

    def upsert_message_event(self, client_id: str, event: dict[str, Any]) -> bool:
        conversation_id = str(event.get("conversation_id") or "default").strip()
        role = str(event.get("role") or "").strip()
        content = str(event.get("content") or "")
        if not conversation_id or role not in {"system", "user", "assistant"} or not content:
            return False
        self.ensure_conversation(client_id, conversation_id)
        message_uid = str(event.get("message_id") or event.get("message_uid") or "").strip() or None
        created_at = str(event.get("created_at") or "").strip() or None
        device_id = str(event.get("device_id") or "unknown").strip()[:128]
        client_created_at = str(event.get("client_created_at") or created_at or "").strip() or None
        content_hash = self.message_hash(conversation_id, role, content)
        if not message_uid:
            seed = "|".join([conversation_id, role, content_hash, client_created_at or ""])
            message_uid = "evt-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                """
                select 1 from messages
                 where message_uid = ? or (conversation_id = ? and role = ? and content_hash = ? and coalesce(client_created_at, created_at, '') = ?)
                 limit 1
                """,
                (message_uid, conversation_id, role, content_hash, client_created_at or ""),
            ).fetchone()
            if existing is not None:
                return False
            conn.execute(
                """
                insert into messages (conversation_id, role, content, created_at, message_uid, device_id, client_created_at, content_hash)
                values (?, ?, ?, coalesce(?, current_timestamp), ?, ?, ?, ?)
                """,
                (conversation_id, role, content, created_at, message_uid, device_id, client_created_at, content_hash),
            )
            conn.execute(
                "update conversations set updated_at = max(updated_at, coalesce(?, current_timestamp)) where id = ?",
                (created_at, conversation_id),
            )
        return True

    def recent_messages(self, conversation_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                select message_uid as message_id, conversation_id, role, content,
                       created_at, device_id, client_created_at, content_hash
                  from messages
                 where conversation_id = ?
                 order by id desc
                 limit ?
                """,
                (conversation_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def alien_glossary(self, conversation_id: str) -> dict[str, str]:
        conversation = self.ensure_conversation("unknown", conversation_id)
        return json.loads(conversation.get("alien_glossary_json") or "{}")

    def update_alien_glossary(self, client_id: str, conversation_id: str, terms: dict[str, str]) -> None:
        if not terms:
            return
        conversation = self.ensure_conversation(client_id, conversation_id)
        glossary = json.loads(conversation.get("alien_glossary_json") or "{}")
        glossary.update(terms)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                update conversations
                   set alien_glossary_json = ?, updated_at = current_timestamp
                 where id = ?
                """,
                (json.dumps(glossary, ensure_ascii=False, sort_keys=True), conversation_id),
            )

    def remember(self, client_id: str, kind: str, content: str, importance: int = 1) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert into memories (client_id, kind, content, importance)
                values (?, ?, ?, ?)
                """,
                (client_id, kind, content, importance),
            )
        self.add_memory_cell(
            client_id=client_id,
            kind="manual",
            summary=content[:240],
            full_content=content,
            topics=[kind],
            importance=max(importance, 4),
        )

    def add_memory_cell(
        self,
        client_id: str,
        kind: str,
        summary: str,
        full_content: str,
        topics: list[str] | None = None,
        importance: int = 1,
    ) -> None:
        clean_topics = topics or []
        embedding = self._memory_embedding(summary, full_content, clean_topics)
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                """
                select id from memory_cells
                 where client_id = ? and lower(summary) = lower(?)
                 limit 1
                """,
                (client_id, summary),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    """
                    update memory_cells
                       set kind = ?, full_content = ?, topics_json = ?, embedding_json = ?,
                           importance = max(importance, ?), updated_at = current_timestamp
                     where id = ?
                    """,
                    (
                        kind,
                        full_content,
                        json.dumps(clean_topics, ensure_ascii=False),
                        json.dumps(embedding),
                        importance,
                        existing["id"],
                    ),
                )
                return
            conn.execute(
                """
                insert into memory_cells (client_id, kind, summary, full_content, topics_json, embedding_json, importance)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_id,
                    kind,
                    summary,
                    full_content,
                    json.dumps(clean_topics, ensure_ascii=False),
                    json.dumps(embedding),
                    importance,
                ),
            )

    def list_memories(self, client_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                select id, kind, summary, full_content, importance, created_at
                  from memory_cells
                 where client_id = ?
                 order by importance desc, id desc
                 limit ?
                """,
                (client_id, limit),
            ).fetchall()
        return [dict(row) | {"content": row["summary"]} for row in rows]

    def memory_summaries_for_prompt(self, client_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._memory_rows(client_id)
        important = [row for row in rows if int(row["importance"]) >= 4 or row["kind"] == "important"]
        thematic = [row for row in rows if row not in important]
        scored = self._score_rows(query, thematic)
        selected = important[: max(3, limit // 2)] + [row for _, row in scored[:limit]]
        return self._dedupe_limited(selected, limit)

    def search_memory_cells(self, client_id: str, queries: list[str], limit: int = 5) -> list[dict[str, Any]]:
        query_text = " ".join(queries)
        rows = self._memory_rows(client_id)
        scored = [item for item in self._score_rows(query_text, rows) if item[0] > 0.12 or int(item[1]["importance"]) >= 5]
        selected = [row for _, row in scored[:limit]]
        if selected:
            ids = [row["id"] for row in selected]
            placeholders = ",".join("?" for _ in ids)
            with self._lock, self._connect() as conn:
                conn.execute(
                    f"update memory_cells set last_used_at = current_timestamp where id in ({placeholders})",
                    ids,
                )
        return selected

    def memories_for_prompt(self, client_id: str, limit: int = 10) -> list[str]:
        rows = self.memory_summaries_for_prompt(client_id, "", limit=limit)
        if rows:
            return [f"{row['kind']}: {row['summary']}" for row in rows]
        with self._lock, self._connect() as conn:
            old_rows = conn.execute(
                """
                select kind, content
                  from memories
                 where client_id = ?
                 order by importance desc, id desc
                 limit ?
                """,
                (client_id, limit),
            ).fetchall()
        return [f"{row['kind']}: {row['content']}" for row in old_rows]

    def add_timed_memory(
        self,
        client_id: str,
        conversation_id: str,
        summary: str,
        full_content: str,
        due_at: str,
        timezone_name: str = "Europe/Moscow",
        source: str = "model",
    ) -> dict[str, Any]:
        self.ensure_conversation(client_id, conversation_id)
        seed = "|".join([client_id, conversation_id, summary, full_content, due_at])
        timer_uid = "tmr-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert into timed_memories
                    (timer_uid, client_id, conversation_id, summary, full_content, due_at, timezone, source)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(timer_uid) do update set
                    summary = excluded.summary,
                    full_content = excluded.full_content,
                    due_at = excluded.due_at,
                    timezone = excluded.timezone,
                    status = 'scheduled',
                    triggered_at = null,
                    materialized_message_uid = null
                """,
                (timer_uid, client_id, conversation_id, summary, full_content, due_at, timezone_name, source),
            )
            row = conn.execute("select * from timed_memories where timer_uid = ?", (timer_uid,)).fetchone()
        return dict(row)

    def list_timed_memories(
        self,
        client_id: str,
        conversation_id: str | None = None,
        include_triggered: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        where = ["client_id = ?"]
        params: list[Any] = [client_id]
        if conversation_id:
            where.append("conversation_id = ?")
            params.append(conversation_id)
        if not include_triggered:
            where.append("status = 'scheduled'")
        params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                select * from timed_memories
                 where {' and '.join(where)}
                 order by due_at asc, id asc
                 limit ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def materialize_due_timed_memories(self, client_id: str, conversation_id: str, now_utc: str) -> list[dict[str, Any]]:
        self.ensure_conversation(client_id, conversation_id)
        with self._lock, self._connect() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    select * from timed_memories
                     where client_id = ? and conversation_id = ? and status = 'scheduled' and due_at <= ?
                     order by due_at asc, id asc
                     limit 10
                    """,
                    (client_id, conversation_id, now_utc),
                ).fetchall()
            ]
            for row in rows:
                message_uid = f"timer-{row['timer_uid']}"
                content = f"⏰ Временная память сработала: {row['summary']}\n{row['full_content']}"
                content_hash = self.message_hash(conversation_id, "system", content)
                conn.execute(
                    """
                    insert or ignore into messages (conversation_id, role, content, message_uid, device_id, client_created_at, content_hash)
                    values (?, 'system', ?, ?, 'timer', ?, ?)
                    """,
                    (conversation_id, content, message_uid, row["due_at"], content_hash),
                )
                conn.execute(
                    """
                    update timed_memories
                       set status = 'triggered', triggered_at = ?, materialized_message_uid = ?
                     where id = ?
                    """,
                    (now_utc, message_uid, row["id"]),
                )
            if rows:
                conn.execute("update conversations set updated_at = current_timestamp where id = ?", (conversation_id,))
        return rows

    def export_client_snapshot(self, client_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            conversations = [
                dict(row)
                for row in conn.execute(
                    """
                    select id, client_id, persona, alien_glossary_json, created_at, updated_at
                      from conversations
                     where client_id = ?
                     order by updated_at, id
                    """,
                    (client_id,),
                ).fetchall()
            ]
            conversation_ids = [row["id"] for row in conversations]
            messages: list[dict[str, Any]] = []
            if conversation_ids:
                placeholders = ",".join("?" for _ in conversation_ids)
                messages = [
                    dict(row)
                    for row in conn.execute(
                        f"""
                        select conversation_id, role, content, created_at
                          from messages
                         where conversation_id in ({placeholders})
                         order by id
                        """,
                        conversation_ids,
                    ).fetchall()
                ]
            memories = [
                dict(row)
                for row in conn.execute(
                    """
                    select client_id, kind, content, importance, created_at
                      from memories
                     where client_id = ?
                     order by id
                    """,
                    (client_id,),
                ).fetchall()
            ]
            memory_cells = [
                dict(row)
                for row in conn.execute(
                    """
                    select client_id, kind, summary, full_content, topics_json, embedding_json,
                           importance, created_at, updated_at, last_used_at
                      from memory_cells
                     where client_id = ?
                     order by id
                    """,
                    (client_id,),
                ).fetchall()
            ]
            timed_memories = [
                dict(row)
                for row in conn.execute(
                    """
                    select timer_uid, client_id, conversation_id, summary, full_content, due_at,
                           timezone, status, source, created_at, triggered_at, materialized_message_uid
                      from timed_memories
                     where client_id = ?
                     order by due_at, id
                    """,
                    (client_id,),
                ).fetchall()
            ]
        return {
            "format": "ai-assistant-snapshot-v1",
            "client_id": client_id,
            "conversations": conversations,
            "messages": messages,
            "memories": memories,
            "memory_cells": memory_cells,
            "timed_memories": timed_memories,
        }

    def import_client_snapshot(self, client_id: str, snapshot: dict[str, Any]) -> dict[str, int]:
        if snapshot.get("format") != "ai-assistant-snapshot-v1":
            raise ValueError("Unsupported snapshot format")
        if snapshot.get("client_id") not in {None, "", client_id}:
            raise ValueError("Snapshot client_id does not match request client_id")

        conversations = snapshot.get("conversations") or []
        messages = snapshot.get("messages") or []
        memories = snapshot.get("memories") or []
        memory_cells = snapshot.get("memory_cells") or []
        timed_memories = snapshot.get("timed_memories") or []
        if not all(isinstance(items, list) for items in [conversations, messages, memories, memory_cells, timed_memories]):
            raise ValueError("Snapshot sections must be arrays")

        counts = {"conversations": 0, "messages": 0, "memories": 0, "memory_cells": 0, "timed_memories": 0}
        with self._lock, self._connect() as conn:
            for item in conversations:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                conversation_id = str(item["id"])
                persona = str(item.get("persona") or Persona.ANA.value)
                if persona not in {Persona.ANA.value, Persona.ALIEN.value}:
                    persona = Persona.ANA.value
                glossary = str(item.get("alien_glossary_json") or "{}")
                conn.execute(
                    """
                    insert into conversations (id, client_id, persona, alien_glossary_json, created_at, updated_at)
                    values (?, ?, ?, ?, coalesce(?, current_timestamp), coalesce(?, current_timestamp))
                    on conflict(id) do update set
                        client_id = excluded.client_id,
                        persona = excluded.persona,
                        alien_glossary_json = excluded.alien_glossary_json,
                        updated_at = max(conversations.updated_at, excluded.updated_at)
                    """,
                    (
                        conversation_id,
                        client_id,
                        persona,
                        glossary,
                        item.get("created_at"),
                        item.get("updated_at"),
                    ),
                )
                counts["conversations"] += 1

            for item in messages:
                if not isinstance(item, dict):
                    continue
                conversation_id = str(item.get("conversation_id") or "").strip()
                role = str(item.get("role") or "").strip()
                content = str(item.get("content") or "")
                created_at = str(item.get("created_at") or "")
                message_uid = str(item.get("message_id") or item.get("message_uid") or "").strip() or None
                device_id = str(item.get("device_id") or "snapshot").strip()[:128]
                client_created_at = str(item.get("client_created_at") or created_at or "").strip() or None
                if not conversation_id or role not in {"user", "assistant", "system"} or not content:
                    continue
                conn.execute(
                    """
                    insert into conversations (id, client_id, persona)
                    values (?, ?, ?)
                    on conflict(id) do nothing
                    """,
                    (conversation_id, client_id, Persona.ANA.value),
                )
                content_hash = self.message_hash(conversation_id, role, content)
                if not message_uid:
                    seed = "|".join([conversation_id, role, content_hash, client_created_at or created_at or ""])
                    message_uid = "snap-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
                exists = conn.execute(
                    """
                    select 1 from messages
                     where message_uid = ?
                        or (conversation_id = ? and role = ? and content_hash = ? and coalesce(client_created_at, created_at, '') = ?)
                     limit 1
                    """,
                    (message_uid, conversation_id, role, content_hash, client_created_at or created_at or ""),
                ).fetchone()
                if exists is None:
                    conn.execute(
                        """
                        insert into messages (conversation_id, role, content, created_at, message_uid, device_id, client_created_at, content_hash)
                        values (?, ?, ?, coalesce(?, current_timestamp), ?, ?, ?, ?)
                        """,
                        (conversation_id, role, content, created_at or None, message_uid, device_id, client_created_at, content_hash),
                    )
                    counts["messages"] += 1
            for item in memories:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or "manual")
                content = str(item.get("content") or "")
                if not content:
                    continue
                importance = int(item.get("importance") or 1)
                exists = conn.execute(
                    """
                    select 1 from memories
                     where client_id = ? and kind = ? and content = ?
                     limit 1
                    """,
                    (client_id, kind, content),
                ).fetchone()
                if exists is None:
                    conn.execute(
                        """
                        insert into memories (client_id, kind, content, importance, created_at)
                        values (?, ?, ?, ?, coalesce(?, current_timestamp))
                        """,
                        (client_id, kind, content, importance, item.get("created_at")),
                    )
                    counts["memories"] += 1

            for item in memory_cells:
                if not isinstance(item, dict):
                    continue
                summary = str(item.get("summary") or "").strip()
                full_content = str(item.get("full_content") or "").strip()
                if not summary or not full_content:
                    continue
                kind = str(item.get("kind") or "thematic")
                topics_json = str(item.get("topics_json") or "[]")
                embedding_json = str(item.get("embedding_json") or "[]")
                importance = int(item.get("importance") or 1)
                existing = conn.execute(
                    """
                    select id from memory_cells
                     where client_id = ? and lower(summary) = lower(?)
                     limit 1
                    """,
                    (client_id, summary),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        insert into memory_cells
                            (client_id, kind, summary, full_content, topics_json, embedding_json,
                             importance, created_at, updated_at, last_used_at)
                        values (?, ?, ?, ?, ?, ?, ?, coalesce(?, current_timestamp), coalesce(?, current_timestamp), ?)
                        """,
                        (
                            client_id,
                            kind,
                            summary,
                            full_content,
                            topics_json,
                            embedding_json,
                            importance,
                            item.get("created_at"),
                            item.get("updated_at"),
                            item.get("last_used_at"),
                        ),
                    )
                    counts["memory_cells"] += 1
                else:
                    conn.execute(
                        """
                        update memory_cells
                           set kind = ?, full_content = ?, topics_json = ?, embedding_json = ?,
                               importance = max(importance, ?), updated_at = current_timestamp
                         where id = ?
                        """,
                        (kind, full_content, topics_json, embedding_json, importance, existing["id"]),
                    )

            for item in timed_memories:
                if not isinstance(item, dict):
                    continue
                summary = str(item.get("summary") or "").strip()
                full_content = str(item.get("full_content") or "").strip()
                due_at = str(item.get("due_at") or "").strip()
                conversation_id = str(item.get("conversation_id") or "default").strip()
                if not summary or not full_content or not due_at or not conversation_id:
                    continue
                seed = "|".join([client_id, conversation_id, summary, full_content, due_at])
                timer_uid = str(item.get("timer_uid") or "").strip() or "tmr-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
                conn.execute(
                    """
                    insert into timed_memories
                        (timer_uid, client_id, conversation_id, summary, full_content, due_at,
                         timezone, status, source, created_at, triggered_at, materialized_message_uid)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, coalesce(?, current_timestamp), ?, ?)
                    on conflict(timer_uid) do update set
                        summary = excluded.summary,
                        full_content = excluded.full_content,
                        due_at = excluded.due_at,
                        timezone = excluded.timezone,
                        status = excluded.status,
                        triggered_at = excluded.triggered_at,
                        materialized_message_uid = excluded.materialized_message_uid
                    """,
                    (
                        timer_uid,
                        client_id,
                        conversation_id,
                        summary,
                        full_content,
                        due_at,
                        str(item.get("timezone") or "Europe/Moscow"),
                        str(item.get("status") or "scheduled"),
                        str(item.get("source") or "snapshot"),
                        item.get("created_at"),
                        item.get("triggered_at"),
                        item.get("materialized_message_uid"),
                    ),
                )
                counts["timed_memories"] += 1
        return counts

    def sync_messages(
        self,
        client_id: str,
        conversation_id: str,
        events: list[dict[str, Any]],
        limit: int = 400,
    ) -> dict[str, Any]:
        self.ensure_conversation(client_id, conversation_id)
        imported = 0
        for event in events:
            if isinstance(event, dict):
                event = dict(event)
                event.setdefault("conversation_id", conversation_id)
                if self.upsert_message_event(client_id, event):
                    imported += 1
        conversation = self.ensure_conversation(client_id, conversation_id)
        return {
            "ok": True,
            "conversation_id": conversation_id,
            "persona": conversation["persona"],
            "imported": imported,
            "messages": self.recent_messages(conversation_id, limit),
            "state": self.conversation_state(conversation_id),
        }

    def upsert_telegram_user(
        self,
        telegram_user_id: str,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
        is_bot: bool = False,
    ) -> dict[str, Any]:
        guest_client_id = "tg-guest-" + hashlib.sha256(telegram_user_id.encode("utf-8")).hexdigest()[:16]
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert into telegram_users
                    (telegram_user_id, guest_client_id, username, first_name, last_name, is_bot, updated_at)
                values (?, ?, ?, ?, ?, ?, current_timestamp)
                on conflict(telegram_user_id) do update set
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    is_bot = excluded.is_bot,
                    updated_at = current_timestamp
                """,
                (telegram_user_id, guest_client_id, username, first_name, last_name, 1 if is_bot else 0),
            )
            row = conn.execute("select * from telegram_users where telegram_user_id = ?", (telegram_user_id,)).fetchone()
        return dict(row)

    def get_telegram_user(self, telegram_user_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("select * from telegram_users where telegram_user_id = ?", (telegram_user_id,)).fetchone()
        return dict(row) if row is not None else None

    def link_telegram_user(self, telegram_user_id: str, app_client_id: str) -> dict[str, Any]:
        clean_client_id = app_client_id.strip()[:128]
        if not clean_client_id:
            raise ValueError("app_client_id is required")
        with self._lock, self._connect() as conn:
            existing = conn.execute("select * from telegram_users where telegram_user_id = ?", (telegram_user_id,)).fetchone()
            if existing is None:
                guest_client_id = "tg-guest-" + hashlib.sha256(telegram_user_id.encode("utf-8")).hexdigest()[:16]
                conn.execute(
                    """
                    insert into telegram_users (telegram_user_id, guest_client_id, app_client_id, is_authorized)
                    values (?, ?, ?, 1)
                    """,
                    (telegram_user_id, guest_client_id, clean_client_id),
                )
            else:
                conn.execute(
                    """
                    update telegram_users
                       set app_client_id = ?, is_authorized = 1, updated_at = current_timestamp
                     where telegram_user_id = ?
                    """,
                    (clean_client_id, telegram_user_id),
                )
            row = conn.execute("select * from telegram_users where telegram_user_id = ?", (telegram_user_id,)).fetchone()
        return dict(row)

    def unlink_telegram_user(self, telegram_user_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                update telegram_users
                   set app_client_id = null, is_authorized = 0, updated_at = current_timestamp
                 where telegram_user_id = ?
                """,
                (telegram_user_id,),
            )
            row = conn.execute("select * from telegram_users where telegram_user_id = ?", (telegram_user_id,)).fetchone()
        return dict(row) if row is not None else None

    def telegram_client_context(self, telegram_user_id: str) -> tuple[str, bool]:
        user = self.get_telegram_user(telegram_user_id)
        if user is None:
            guest_client_id = "tg-guest-" + hashlib.sha256(telegram_user_id.encode("utf-8")).hexdigest()[:16]
            return guest_client_id, False
        if int(user.get("is_authorized") or 0) and user.get("app_client_id"):
            return str(user["app_client_id"]), True
        return str(user.get("guest_client_id") or "tg-guest-" + hashlib.sha256(telegram_user_id.encode("utf-8")).hexdigest()[:16]), False

    def conversation_state(self, conversation_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                select count(*) as message_count, max(created_at) as last_message_at, max(id) as revision
                  from messages
                 where conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        return dict(row) if row is not None else {"message_count": 0, "last_message_at": None, "revision": 0}

    @staticmethod
    def message_hash(conversation_id: str, role: str, content: str) -> str:
        return hashlib.sha256("\n".join([conversation_id, role, content]).encode("utf-8")).hexdigest()
    def _memory_rows(self, client_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    select id, kind, summary, full_content, topics_json, embedding_json, importance
                      from memory_cells
                     where client_id = ?
                     order by importance desc, id desc
                     limit 300
                    """,
                    (client_id,),
                ).fetchall()
            ]

    def _score_rows(self, query: str, rows: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
        query_embedding = embed_text(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            topics_json = row.get("topics_json", "")
            lexical = score_text(query, row["summary"], row["full_content"], topics_json)
            semantic = cosine_similarity(query_embedding, self._row_embedding(row))
            score = (lexical * 0.35) + semantic + (int(row["importance"]) * 0.03)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], int(item[1]["importance"]), int(item[1]["id"])), reverse=True)
        return scored

    def _row_embedding(self, row: dict[str, Any]) -> list[float]:
        try:
            embedding = json.loads(row.get("embedding_json") or "[]")
        except json.JSONDecodeError:
            embedding = []
        if isinstance(embedding, list) and embedding:
            return [float(value) for value in embedding]
        topics = json.loads(row.get("topics_json") or "[]")
        return self._memory_embedding(row["summary"], row["full_content"], topics)

    def _memory_embedding(self, summary: str, full_content: str, topics: list[str]) -> list[float]:
        return embed_text("\n".join([summary, full_content, " ".join(topics)]))

    def _dedupe_limited(self, rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[int] = set()
        for row in rows:
            memory_id = int(row["id"])
            if memory_id not in seen:
                seen.add(memory_id)
                deduped.append(row)
            if len(deduped) >= limit:
                break
        return deduped
