import json
import unittest
from unittest.mock import patch, MagicMock

from app import web_search
from app.smart_memory import extract_memory_directive
from app.capabilities import CAPABILITIES_PROMPT
import simple_server as server
from tests.test_simple_server_stdlib import isolated_storage


class SearchTests(unittest.TestCase):
    def test_creator(self):
        self.assertIn('@Ne_gr_idi_rabotai', CAPABILITIES_PROMPT)

    def test_directive_hidden(self):
        for text in ['{"web_search":{"query":"погода"}}', '```assistant_memory\n{"web_search":{"query":"погода"}}\n```']:
            visible, directive = extract_memory_directive(text)
            self.assertEqual(visible, '')
            self.assertEqual(directive.web_search, 'погода')

    def test_disabled_no_network(self):
        with patch.dict('os.environ', {'TAVILY_API_KEY': ''}), patch.object(web_search, 'urlopen') as net:
            self.assertIn('error', web_search.search('тест'))
            net.assert_not_called()

    def test_network_contract_and_bounds(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'results': [{'url':'https://example.org', 'content':'a'*9000}] * 8}).encode()
        with patch.dict('os.environ', {'TAVILY_API_KEY': 'test-key'}), patch.object(web_search, 'urlopen', return_value=response) as net:
            result = web_search.search('test')
            request = net.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-key')
            self.assertEqual(json.loads(request.data)['max_results'], 3)
            self.assertEqual(len(result['results']), 3)
            self.assertEqual(len(result['results'][0]['content']), 2000)
            self.assertIn('error', web_search.search('x'*501))
            self.assertEqual(net.call_count, 1)

    def test_errors_hide_credentials(self):
        with patch.dict('os.environ', {'TAVILY_API_KEY': 'test-secret'}), patch.object(web_search, 'urlopen', side_effect=RuntimeError('test-secret')):
            result = web_search.search('test')
            self.assertIn('error', result)
            self.assertNotIn('test-secret', str(result))

    def test_tool_followup_is_bounded_and_no_web_side_effects(self):
        responses = [('{"web_search":{"query":"test"}}', 'mock'), ('Ответ со ссылкой https://example.org\n```assistant_memory\n{"web_search":{"query":"again"},"remember":[{"summary":"untrusted","full":"untrusted"}],"telegram_action":{"mute":{"telegram_user_id":1}}}\n```', 'mock')]
        with isolated_storage(), patch.object(server, 'complete', side_effect=responses) as complete, patch.object(server, 'tavily_search', return_value={'results': []}) as search:
            result = server.complete_with_smart_memory('test', 'test', [{'role':'user', 'content':'Найди'}])
            self.assertIn('https://example.org', result[0])
            self.assertEqual(result[2], 0)
            self.assertIsNone(result[-1])
            self.assertEqual(complete.call_count, 2)
            search.assert_called_once_with('test')
