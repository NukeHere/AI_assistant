"""SQLite-style row access for the libsql Python driver's remote connections."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any


class LibsqlRow(Mapping[str, Any]):
    def __init__(self, columns: tuple[str, ...], values: tuple[Any, ...]):
        self._columns = columns
        self._values = values

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._columns.index(key)]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)


class LibsqlCursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    def _convert(self, values: tuple[Any, ...] | None) -> LibsqlRow | None:
        if values is None:
            return None
        columns = tuple(column[0] for column in self._cursor.description)
        return LibsqlRow(columns, values)

    def fetchone(self) -> LibsqlRow | None:
        return self._convert(self._cursor.fetchone())

    def fetchall(self) -> list[LibsqlRow]:
        return [self._convert(row) for row in self._cursor.fetchall()]

    def __iter__(self) -> Iterator[LibsqlRow]:
        while (row := self.fetchone()) is not None:
            yield row

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class LibsqlConnection:
    def __init__(self, connection: Any):
        self._connection = connection

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> LibsqlCursor:
        return LibsqlCursor(self._connection.execute(sql, parameters))

    def executemany(self, sql: str, parameters: list[tuple[Any, ...]]) -> LibsqlCursor:
        return LibsqlCursor(self._connection.executemany(sql, parameters))

    def executescript(self, script: str) -> LibsqlCursor:
        return LibsqlCursor(self._connection.executescript(script))

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()
