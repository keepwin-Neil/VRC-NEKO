"""host_motion_tools=false：N.E.K.O 主对话拿不到任何 npc.* 动作工具，提示词改为只负责说话。"""
import asyncio
import json
import unittest

import _bootstrap  # noqa: F401
from test_plugin_config import _plugin_class
from yui_npc_controller.runtime.config import YuiPluginConfig
from yui_npc_controller.runtime.tool_surface import YuiToolSurface


class HostMotionToolsOffTests(unittest.TestCase):
    def test_config_parses_switch_and_defaults_to_upstream_behaviour(self):
        self.assertTrue(YuiPluginConfig().host_motion_tools)
        self.assertFalse(YuiPluginConfig.from_mapping({"host_motion_tools": False}).host_motion_tools)

    def test_surface_host_definitions_empty_when_disabled(self):
        surface = YuiToolSurface.__new__(YuiToolSurface)
        surface.host_motion_tools = False
        self.assertEqual(surface.host_definitions(), [])

    def test_startup_registers_no_tools_and_context_says_body_is_automatic(self):
        plugin = _plugin_class()(None)
        pushed, registered = [], []

        async def load_config():
            return YuiPluginConfig.from_mapping({"host_motion_tools": False})

        plugin._load_config = load_config
        plugin._configure_reply_display = lambda: None
        plugin._start_log_tailer = lambda: None
        plugin.register_llm_tool = lambda **kwargs: registered.append(kwargs["name"])
        plugin.push_message = lambda **kwargs: pushed.append(kwargs) or {"ok": True}

        result = asyncio.run(plugin.startup())

        self.assertEqual(result.value["status"], "ready")
        self.assertEqual(registered, [])
        self.assertEqual(result.value["result"]["llm_tools"], [])
        payload = json.loads(pushed[-1]["parts"][0]["text"].removeprefix("YUI_WORLD_CONTEXT "))
        self.assertEqual(payload["available_tools"], [])
        text = "".join(payload["instructions"])
        self.assertIn("独立动作系统", text)
        self.assertNotIn("npc.stop 可调用", text)

    def test_connected_context_replaces_must_call_tool_rule(self):
        plugin = _plugin_class()(None)
        plugin._config = YuiPluginConfig.from_mapping({"host_motion_tools": False})
        lines = plugin._host_action_instructions()
        self.assertTrue(any("由独立的动作系统" in line for line in lines))
        self.assertFalse(any("必须在回复前调用" in line for line in lines))
        plugin._config = YuiPluginConfig()
        self.assertTrue(any("必须在回复前调用" in line for line in plugin._host_action_instructions()))
