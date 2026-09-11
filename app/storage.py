from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.embeddings import cosine_similarity, embed_text
from app.personas import Persona
from app.smart_memory import score_text


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

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"pragma table_info({table})")}
        if column not in columns:
            conn.execute(f"alter table {table} add column {column} {definition}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
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

    def add_message(self, conversation_id: str, role: str, content: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "insert into messages (conversation_id, role, content) values (?, ?, ?)",
                (conversation_id, role, content),
            )
            conn.execute(
                "update conversations set updated_at = current_timestamp where id = ?",
                (conversation_id,),
            )

    def recent_messages(self, conversation_id: str, limit: int = 20) -> list[dict[str, str]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                select role, content
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
