"""Bounded, pure-Python Hrana HTTP transport for remote libSQL databases."""
from __future__ import annotations

import base64
import json
import io
import threading
import requests
import sqlite3
from urllib.request import Request
from app.libsql_adapter import LibsqlRow


_transport = threading.local()


def urlopen(request, *, timeout):
    """Reuse TLS connections within each worker, without sharing sessions across threads."""
    session = getattr(_transport, "session", None)
    if session is None:
        session = _transport.session = requests.Session()
    try:
        response = session.post(request.full_url, data=request.data,
                                headers=dict(request.header_items()), timeout=timeout,
                                allow_redirects=False)
        with response:
            response.raise_for_status()
            if response.is_redirect:
                raise RuntimeError("Unexpected database HTTP redirect")
            return io.BytesIO(response.content)
    except requests.RequestException as error:
        session.close()
        _transport.session = None
        raise RuntimeError("Database HTTP transport failed: " + type(error).__name__) from error


def encode(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    if isinstance(value, bytes):
        return {"type": "blob", "base64": base64.b64encode(value).decode()}
    return {"type": "text", "value": str(value)}


def decode(value):
    kind = value["type"]
    if kind == "null":
        return None
    if kind == "integer":
        return int(value["value"])
    if kind == "blob":
        return base64.b64decode(value["base64"])
    return value["value"]


class Cursor:
    def __init__(self, result):
        columns = tuple(col["name"] for col in result.get("cols", []))
        self._rows = iter(LibsqlRow(columns, tuple(map(decode, row))) for row in result.get("rows", []))
        self.rowcount = result.get("affected_row_count", 0)
        self.lastrowid = result.get("last_insert_rowid")

    def fetchone(self):
        return next(self._rows, None)

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return self._rows


class HttpConnection:
    def __init__(self, url, auth_token, *, timeout=10):
        self.url = "https://" + url.removeprefix("libsql://")
        self.auth_token = auth_token
        self.timeout = timeout
        self.baton = None
        self.in_transaction = False
        self.closed = False

    def _pipeline(self, requests):
        request = Request(self.url.rstrip("/") + "/v2/pipeline",
                          data=json.dumps({"baton": self.baton, "requests": requests}).encode(),
                          headers={"Authorization": "Bearer " + self.auth_token,
                                   "Content-Type": "application/json"})
        # Never retry a write: a lost response can follow a successful commit.
        with urlopen(request, timeout=self.timeout) as response:
            data = json.load(response)
        self.baton = data.get("baton")
        if data.get("base_url"):
            if not data["base_url"].startswith("https://"):
                raise RuntimeError("Insecure database redirect")
            self.url = data["base_url"]
        results = []
        for item in data["results"]:
            if item["type"] == "error":
                raise RuntimeError("Database request failed: " + item["error"].get("message", "unknown"))
            results.append(item["response"])
        return results

    @staticmethod
    def _statement(sql, parameters=()):
        return {"type": "execute", "stmt": {"sql": sql, "args": list(map(encode, parameters)), "want_rows": True}}

    def execute(self, sql, parameters=()):
        if self.closed:
            raise RuntimeError("Database connection is closed")
        requests = []
        # Match sqlite3's implicit transaction for writes; reads stay autocommit.
        if not self.in_transaction and sql.lstrip().split(None, 1)[0].lower() in {"insert", "update", "delete", "replace"}:
            requests.append(self._statement("BEGIN"))
            self.in_transaction = True
        requests.append(self._statement(sql, parameters))
        return Cursor(self._pipeline(requests)[-1]["result"])

    def executemany(self, sql, parameters):
        if self.closed:
            raise RuntimeError("Database connection is closed")
        cursor = Cursor({})
        parameters = iter(parameters)
        from itertools import islice
        while batch := list(islice(parameters, 100)):
            requests = []
            if not self.in_transaction:
                requests.append(self._statement("BEGIN"))
                self.in_transaction = True
            requests.extend(self._statement(sql, args) for args in batch)
            cursor = Cursor(self._pipeline(requests)[-1]["result"])
        return cursor

    def executescript(self, script):
        self.commit()
        statement = ""
        cursor = Cursor({})
        for char in script:
            statement += char
            if char == ";" and sqlite3.complete_statement(statement):
                cursor = self.execute(statement)
                statement = ""
        if statement.strip():
            cursor = self.execute(statement)
        return cursor

    def commit(self):
        if self.in_transaction:
            self._pipeline([self._statement("COMMIT"), {"type": "close"}])
            self.baton = None
            self.in_transaction = False

    def rollback(self):
        if self.in_transaction and self.baton:
            self._pipeline([self._statement("ROLLBACK"), {"type": "close"}])
            self.baton = None
            self.in_transaction = False

    def close(self):
        if self.baton:
            try:
                self._pipeline([{"type": "close"}])
            except Exception:
                pass  # Server expires abandoned streams; preserve the original error.
        self.closed = True
