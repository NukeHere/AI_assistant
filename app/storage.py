from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.personas import Persona


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
                """
            )

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

    def memories_for_prompt(self, client_id: str, limit: int = 10) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                select kind, content
                  from memories
                 where client_id = ?
                 order by importance desc, id desc
                 limit ?
                """,
                (client_id, limit),
            ).fetchall()
        return [f"{row['kind']}: {row['content']}" for row in rows]
