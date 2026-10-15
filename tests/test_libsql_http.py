import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.libsql_http import HttpConnection, encode, decode
from app.storage import Storage


class HttpTests(unittest.TestCase):
    def test_storage_transactions_and_rows_over_pipeline(self):
        db = sqlite3.connect(':memory:')
        requests = []

        def urlopen(request, *, timeout):
            self.assertEqual(timeout, 10)
            self.assertEqual(request.full_url, 'https://example/v2/pipeline')
            payload = json.loads(request.data)
            requests.append(payload)
            results = []
            for operation in payload['requests']:
                if operation['type'] == 'close':
                    results.append({'type': 'ok', 'response': {'type': 'close'}})
                    continue
                stmt = operation['stmt']
                cursor = db.execute(stmt['sql'], tuple(map(decode, stmt['args'])))
                result = {'cols': [{'name': col[0]} for col in (cursor.description or [])],
                          'rows': [[encode(v) for v in row] for row in cursor.fetchall()],
                          'affected_row_count': max(0, cursor.rowcount),
                          'last_insert_rowid': str(cursor.lastrowid)}
                results.append({'type': 'ok', 'response': {'type': 'execute', 'result': result}})
            return io.BytesIO(json.dumps({'baton': 'session', 'results': results}).encode())

        with tempfile.TemporaryDirectory() as directory, patch('app.libsql_http.urlopen', side_effect=urlopen):
            storage = Storage(Path(directory)/'unused', remote_url='libsql://example', auth_token='secret')
            storage.init()
            storage.ensure_conversation('client', 'conversation')
            storage.add_message('conversation', 'user', 'hello')
            self.assertEqual(storage.recent_messages('conversation', 10)[0]['content'], 'hello')
            with self.assertRaises(ValueError):
                with storage._connect() as conn:
                    conn.execute("insert into conversations(id, client_id) values (?, ?)", ('rollback', 'client'))
                    raise ValueError('abort')
            self.assertIsNone(db.execute("select id from conversations where id='rollback'").fetchone())
        self.assertTrue(any(r['baton'] == 'session' for r in requests))
        db.close()

    def test_bulk_insert_uses_bounded_pipelines(self):
        conn = HttpConnection('libsql://example', 'secret')
        with patch.object(conn, '_pipeline', return_value=[{'result': {}}]) as send:
            conn.executemany('insert into t values (?)', [(n,) for n in range(250)])
            self.assertEqual(send.call_count, 3)
            self.assertEqual(len(send.call_args_list[0].args[0]), 101)
            self.assertEqual(len(send.call_args_list[-1].args[0]), 50)

    def test_http_session_reuses_connections_without_retrying_writes(self):
        from app import libsql_http
        from urllib.request import Request
        from unittest.mock import MagicMock
        libsql_http._transport.session = None
        response = MagicMock()
        response.is_redirect = False
        response.content = b'{}'
        with patch.object(libsql_http.requests, 'Session') as make:
            make.return_value.post.return_value = response
            for _ in range(2):
                libsql_http.urlopen(Request('https://example/v2/pipeline', data=b'{}'), timeout=10)
            make.assert_called_once()
            self.assertEqual(make.return_value.post.call_count, 2)
        libsql_http._transport.session = None

    def test_timeout_is_propagated_without_retry(self):
        conn = HttpConnection('libsql://example', 'secret')
        with patch('app.libsql_http.urlopen', side_effect=TimeoutError) as send:
            with self.assertRaises(TimeoutError):
                conn.execute('select 1')
            send.assert_called_once()

    def test_sql_error_is_propagated(self):
        data = {'baton': 'session', 'results': [{'type': 'error', 'error': {'message': 'bad SQL'}}]}
        with patch('app.libsql_http.urlopen', return_value=io.BytesIO(json.dumps(data).encode())):
            with self.assertRaisesRegex(RuntimeError, 'bad SQL'):
                HttpConnection('libsql://example', 'secret').execute('select broken')
