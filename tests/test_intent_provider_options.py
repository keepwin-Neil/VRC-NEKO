"""意图模型请求选项：额外请求字段（关闭思考）、输出格式固定与 json_schema 不支持时的记忆回退。"""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

import _bootstrap  # noqa: F401
from test_intent import _context, _envelope, _intent
from yui_npc_controller.runtime.config import YuiIntentModelConfig
from yui_npc_controller.runtime.intent import AutonomyIntentProvider

DEEPSEEK_400 = json.dumps({"error": {"message": "This response_format type is unavailable now (request_id: x)",
                                     "type": "invalid_request_error"}}).encode()


def _config(**overrides):
    base = {"enabled": True, "endpoint": "https://api.example.com/v1/chat/completions"}
    base.update(overrides)
    return YuiIntentModelConfig.from_mapping(base)


class IntentProviderOptionTests(unittest.TestCase):
    def run_requests(self, config, responder, count=1):
        calls = []

        def http_post(endpoint, headers, body, timeout_s, max_bytes):
            payload = json.loads(body)
            calls.append(payload)
            return responder(payload)

        with patch.dict(os.environ, {"TEST_API": "k"}, clear=False):
            provider = AutonomyIntentProvider(config, http_post=http_post)
            results = [asyncio.run(provider.request(_context())) for _ in range(count)]
        return calls, results

    def test_extra_request_is_merged_into_body(self):
        config = _config(extra_request={"thinking": {"type": "disabled"}}, response_format="json_object")
        calls, results = self.run_requests(config, lambda p: (200, _envelope(_intent())))
        self.assertEqual(results[0]["status"], "succeeded")
        self.assertEqual(calls[0]["thinking"], {"type": "disabled"})
        self.assertEqual(calls[0]["response_format"], {"type": "json_object"})
        self.assertEqual(len(calls), 1)

    def test_extra_request_cannot_override_reserved_fields(self):
        for key in ("model", "messages", "response_format", "stream"):
            with self.assertRaises(ValueError):
                _config(extra_request={key: "x"})
        with self.assertRaises(ValueError):
            _config(response_format="yaml")

    def test_auto_remembers_schema_unsupported_with_deepseek_wording(self):
        def responder(payload):
            if payload["response_format"]["type"] == "json_schema":
                return 400, DEEPSEEK_400
            return 200, _envelope(_intent())

        calls, results = self.run_requests(_config(), responder, count=3)
        self.assertTrue(all(r["status"] == "succeeded" for r in results))
        formats = [c["response_format"]["type"] for c in calls]
        # 第一次：schema 被拒后回退；之后两次直接 json_object，没有多余的失败往返。
        self.assertEqual(formats, ["json_schema", "json_object", "json_object", "json_object"])


if __name__ == "__main__":
    unittest.main()
