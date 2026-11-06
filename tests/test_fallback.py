import io
import json
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

from gemini import translate_batch


class FallbackTest(unittest.TestCase):
    def setUp(self):
        settings = patch('gemini.read_settings', return_value={'provider': 'gemini', 'profiles': {}})
        settings.start()
        self.addCleanup(settings.stop)

    def response(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            'status': 'completed', 'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': '[{"id":"1","translation":"Xin chào"}]'}]}]
        }).encode()
        return response

    def error(self, code):
        return HTTPError('https://example.test', code, 'error', {}, io.BytesIO(b'error'))

    def run_batch(self, effects):
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test', 'GEMINI_MODEL': 'primary',
                                      'GEMINI_FALLBACK_MODELS': 'fallback,primary,fallback'}), \
             patch('gemini.open_translation', side_effect=effects) as call, \
             patch('time.sleep') as sleep:
            result = translate_batch([{'index': '1', 'text': '你好'}])
            return result, call, sleep

    def test_partial_response_recovers_only_missing_ids(self):
        batch = [{'index': '1', 'text': 'a'}, {'index': '2', 'text': 'b'}]
        with patch('gemini._request_translations', side_effect=[
            {'2': 'hai'}, {'1': 'một'}
        ]) as request:
            self.assertEqual(translate_batch(batch, 'French'), ['một', 'hai'])
        self.assertEqual(request.call_args.args[:2], ([batch[0]], 'French'))

    def test_missing_ids_use_smaller_groups_and_stop(self):
        batch = [{'index': str(i), 'text': 'a'} for i in range(1, 24)]
        with patch('gemini._request_translations', return_value={}) as request:
            with self.assertRaisesRegex(RuntimeError, 'sau 3 lượt.*1, 2, 3'):
                translate_batch(batch)
        self.assertEqual([len(call.args[0]) for call in request.call_args_list],
                         [23, 20, 3, 5, 5, 5, 5, 3])

    def test_empty_and_invalid_rows_are_retried(self):
        response = self.response()
        response.__enter__.return_value.read.return_value = json.dumps({
            'status': 'completed', 'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': json.dumps([
                None, {'id': '1', 'translation': None},
                {'id': '1', 'translation': '  '}, {'id': '99', 'translation': 'extra'}
            ])}]}]
        }).encode()
        result, call, _ = self.run_batch([response, self.response()])
        self.assertEqual(result, ['Xin chào'])
        self.assertEqual(call.call_count, 2)

    def test_schema_requires_every_original_id_including_ocr_noise(self):
        batch = [{'index': '14', 'text': 'example.test'}, {'index': '80', 'text': 'GdaGada'}]
        response = self.response()
        response.__enter__.return_value.read.return_value = json.dumps({
            'status': 'completed', 'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': json.dumps({
                '80': '[Không rõ]', '14': 'example.test'
            })}]}]
        }).encode()
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test'}), \
             patch('gemini.open_translation', return_value=response) as request:
            self.assertEqual(translate_batch(batch), ['example.test', '[Không rõ]'])
        body = json.loads(request.call_args.args[0].data)
        self.assertFalse(body['store'])
        self.assertEqual(body['response_format']['mime_type'], 'application/json')
        schema = body['response_format']['schema']
        self.assertEqual(schema['required'], ['14', '80'])
        self.assertEqual(schema['properties'], {'14': {'type': 'string'}, '80': {'type': 'string'}})
        self.assertEqual(request.call_count, 1)

    def test_gemini_thinking_level_matches_model_capability(self):
        from gemini import translation_request

        for model, level in [('gemini-3.8-flash', 'low'), ('gemini-3.5-flash-lite', 'minimal')]:
            request = translation_request('gemini', {'api_key': 'test'}, model, 'OK')
            body = json.loads(request.data)
            self.assertEqual(body['generation_config']['thinking_level'], level)

    def test_busy_or_limited_model_switches_immediately(self):
        for status in (429, 503):
            with self.subTest(status=status):
                result, call, sleep = self.run_batch([self.error(status), self.response()])
                self.assertEqual(result, ['Xin chào'])
                self.assertEqual(call.call_count, 2)
                sleep.assert_not_called()
                self.assertEqual(json.loads(call.call_args.args[0].data)['model'], 'fallback')

    def test_missing_model_switches_immediately(self):
        _, call, sleep = self.run_batch([self.error(404), self.response()])
        self.assertEqual(call.call_count, 2)
        sleep.assert_not_called()

    def test_permission_error_stops(self):
        with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
            self.run_batch([self.error(403)])

    def test_exhaustion_is_bounded_and_duplicates_removed(self):
        with patch.dict('os.environ', {'GEMINI_FALLBACK_MODELS': 'fallback,primary,fallback'}), \
             patch('gemini.open_translation', side_effect=[self.error(503) for _ in range(5)]) as call, \
             patch('time.sleep') as sleep:
            batch = [{'index': str(i), 'text': '你好'} for i in range(178)]
            with self.assertRaisesRegex(RuntimeError, 'HTTP 503'):
                translate_batch(batch, connection=('gemini', {'api_key': 'test', 'model': 'primary'}))
        self.assertEqual(call.call_count, 5)
        sleep.assert_called_once_with(1)

    def test_next_batch_prefers_successful_fallback(self):
        connection = ('gemini', {'api_key': 'test', 'model': 'primary'})
        batch = [{'index': '1', 'text': '你好'}]
        with patch.dict('os.environ', {'GEMINI_FALLBACK_MODELS': 'fallback'}), \
             patch('gemini.open_translation', side_effect=[self.error(503), self.response(), self.response()]) as send:
            for _ in range(2):
                self.assertEqual(translate_batch(batch, connection=connection), ['Xin chào'])
        self.assertEqual(json.loads(send.call_args.args[0].data)['model'], 'fallback')


if __name__ == '__main__':
    unittest.main()
