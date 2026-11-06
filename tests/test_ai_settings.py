import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from ai_settings import profile, read_settings, requested_settings, save_settings
from gemini import probe_connection, translate_batch, translation_profile


class AISettingsTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings_file = Path(directory.name) / "settings.json"
        settings = patch("ai_settings.SETTINGS_FILE", self.settings_file)
        settings.start()
        self.addCleanup(settings.stop)

    def test_save_switch_and_clear_key(self):
        self.assertEqual(read_settings()["provider"], "gemini")
        profile = {"provider": "anthropic", "model": "claude-test"}
        public = save_settings({**profile, "api_key": "private-test-key"})
        self.assertNotIn("private-test-key", json.dumps(public))
        self.assertEqual(self.settings_file.stat().st_mode & 0o777, 0o600)
        save_settings({"provider": "openai", "model": "gpt-test", "api_key": "other-key"})
        self.assertTrue(save_settings(profile)["profiles"]["anthropic"]["has_key"])
        self.assertFalse(save_settings({**profile, "clear_key": True})["profiles"]["anthropic"]["has_key"])

    def test_custom_endpoint_cannot_reuse_key_when_url_changes(self):
        profile = {"provider": "compatible", "model": "test", "base_url": "https://api.example/v1"}
        save_settings({**profile, "api_key": "private-test-key"})
        for url in ("https://other.example/v1", "http://api.example/v1"):
            with self.assertRaises(ValueError):
                save_settings({**profile, "base_url": url})
        self.assertEqual(read_settings()["profiles"]["compatible"]["base_url"], profile["base_url"])

    def test_translates_through_each_new_provider_and_recovers_missing_ids(self):
        for provider, url in (
            ("anthropic", "https://api.anthropic.com/v1/messages"),
            ("openai", "https://api.openai.com/v1/chat/completions"),
            ("compatible", "http://localhost:11434/v1/chat/completions"),
        ):
            with self.subTest(provider=provider):
                save_settings({"provider": provider, "model": "test-model",
                               "base_url": "http://localhost:11434/v1", "api_key": "test-key"})
                responses = []
                for text in ('{"2":"hai"}', '```json\n{"1":"một"}\n```'):
                    body = ({"content": [{"type": "text", "text": text}]} if provider == "anthropic"
                            else {"choices": [{"message": {"content": text}}]})
                    response = MagicMock()
                    response.__enter__.return_value.read.return_value = json.dumps(body).encode()
                    responses.append(response)
                with patch("gemini.open_translation", side_effect=responses) as send:
                    self.assertEqual(translate_batch([{"index": "1", "text": "一"},
                                                      {"index": "2", "text": "二"}]), ["một", "hai"])
                request = send.call_args.args[0]
                self.assertEqual(request.full_url, url)
                body = json.loads(request.data)
                self.assertEqual(body["model"], "test-model")
                prompt = body["messages"][0]["content"]
                payload = json.loads(prompt.split("Input:\n", 1)[1])
                self.assertEqual([str(item["id"]) for item in payload], ["1"])
                self.assertIn("[Không rõ]", prompt)
                if provider == "anthropic":
                    self.assertEqual(request.get_header("X-api-key"), "test-key")
                else:
                    self.assertEqual(request.get_header("Authorization"), "Bearer test-key")

    def test_probe_uses_unsaved_model_with_saved_key_and_does_not_fallback_or_save(self):
        save_settings({"provider": "gemini", "model": "old-model", "api_key": "test-key"})
        settings, provider, fields = requested_settings({"provider": "gemini", "model": "trial-model"})
        settings["profiles"][provider] = fields
        response = {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": "OK"}]}]}
        with patch("gemini.open_translation", return_value=io.BytesIO(json.dumps(response).encode())) as send:
            probe_connection(provider, profile(settings, provider))
        send.assert_called_once()
        request = send.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/v1/interactions"))
        self.assertEqual(json.loads(request.data)["model"], "trial-model")
        self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
        self.assertEqual(read_settings()["profiles"][provider]["model"], "old-model")

    def test_probe_reports_auth_model_and_network_errors_without_exposing_provider_body(self):
        cases = [
            ("gemini", 400, {"details": [{"reason": "API_KEY_INVALID"}]}, "từ chối xác thực"),
            ("openai", 401, {}, "từ chối xác thực"),
            ("openai", 400, {"param": "model"}, "mã model"),
            ("anthropic", 404, {}, "mã model"),
            ("compatible", 404, {}, "Base URL và mã model"),
            ("gemini", 429, {}, "hạn mức"),
            ("gemini", 503, {}, "dịch vụ AI"),
        ]
        for provider, code, details, expected in cases:
            with self.subTest(provider=provider, code=code):
                body = json.dumps({"error": {**details, "message": "private-test-key"}}).encode()
                error = HTTPError("https://example.test", code, "error", {}, io.BytesIO(body))
                config = {"api_key": "private-test-key", "model": "trial-model",
                          "base_url": "https://example.test/v1"}
                with patch("gemini.open_translation", side_effect=error) as send, \
                        self.assertRaises(RuntimeError) as raised:
                    probe_connection(provider, config)
                send.assert_called_once()
                self.assertIn(expected.lower(), str(raised.exception).lower())
                self.assertIn(f"HTTP {code}", str(raised.exception))
                self.assertNotIn("private-test-key", str(raised.exception))
        with patch("gemini.open_translation", side_effect=URLError(TimeoutError())), \
                self.assertRaisesRegex(RuntimeError, "thời gian chờ"):
            probe_connection("gemini", {"api_key": "test", "model": "trial-model"})

    def test_gemini_quota_rotates_and_reuses_working_key_for_next_batch(self):
        public = save_settings({"provider": "gemini", "model": "primary", "api_key": "test-primary",
                                "backup_keys": "test-primary, test-next, test-next"})
        self.assertEqual(public["profiles"]["gemini"]["backup_key_count"], 1)
        self.assertNotIn("test-next", json.dumps(public))
        quota = HTTPError("https://example.test", 429, "quota", {}, io.BytesIO())
        body = json.dumps({"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": '{"1":"Chào"}'}]}]}).encode()
        connection = translation_profile()
        batch = [{"index": "1", "text": "你好"}]
        with patch("gemini.open_translation", side_effect=[quota, io.BytesIO(body), io.BytesIO(body)]) as send, \
                patch("gemini.time.sleep") as sleep:
            self.assertEqual(translate_batch(batch, connection=connection), ["Chào"])
            self.assertEqual(translate_batch(batch, connection=connection), ["Chào"])
        self.assertEqual([call.args[0].get_header("X-goog-api-key") for call in send.call_args_list],
                         ["test-primary", "test-next", "test-next"])
        sleep.assert_not_called()
        self.assertEqual(save_settings({"provider": "gemini", "model": "primary", "clear_backup_keys": True})
                         ["profiles"]["gemini"]["backup_key_count"], 0)

    def test_gemini_tries_fallback_model_after_exhausting_both_keys(self):
        save_settings({"provider": "gemini", "model": "primary", "api_key": "test-primary",
                       "backup_keys": "test-next"})
        quota = [HTTPError("https://example.test", 429, "quota", {}, io.BytesIO()) for _ in range(2)]
        body = json.dumps({"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": '{"1":"Chào"}'}]}]}).encode()
        with patch.dict("os.environ", {"GEMINI_FALLBACK_MODELS": "fallback"}), \
                patch("gemini.open_translation", side_effect=[*quota, io.BytesIO(body)]) as send:
            self.assertEqual(translate_batch([{"index": "1", "text": "你好"}]), ["Chào"])
        models = [json.loads(call.args[0].data)["model"] for call in send.call_args_list]
        self.assertEqual(models, ["primary", "primary", "fallback"])
