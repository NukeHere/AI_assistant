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
                """
            )
            self._ensure_column(conn, "memory_cells", "embedding_json", "text not null default '[]'")
            self._ensure_column(conn, "messages", "message_uid", "text")
            self._ensure_column(conn, "messages", "device_id", "text not null default ''")
            self._ensure_column(conn, "messages", "client_created_at", "text")
            self._ensure_column(conn, "messages", "content_hash", "text not null default ''")
            conn.execute("create unique index if not exists idx_messages_uid on messages (message_uid) where message_uid is not null")
            conn.execute("create index if not exists idx_messages_conversation_created on messages (conversation_id, created_at, id)")

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
        return {
            "format": "ai-assistant-snapshot-v1",
            "client_id": client_id,
            "conversations": conversations,
            "messages": messages,
            "memories": memories,
            "memory_cells": memory_cells,
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
        if not all(isinstance(items, list) for items in [conversations, messages, memories, memory_cells]):
            raise ValueError("Snapshot sections must be arrays")

        counts = {"conversations": 0, "messages": 0, "memories": 0, "memory_cells": 0}
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
