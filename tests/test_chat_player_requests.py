"""玩家原话直达独立动作模型；“过来”走世界验证的玩家路径。"""
from types import SimpleNamespace
import unittest

import _bootstrap  # noqa: F401
import test_autonomy as fixtures
from yui_npc_controller.runtime.intent import IntentModelError, validate_intent
from yui_npc_controller.runtime.pose_codec import encode_stream_control
from yui_npc_controller.runtime.continuous_execution import ContinuousExecution
from yui_npc_controller.runtime.motion_backend import MotionBackend, MotionBackendConfig


def _value(first, second=None):
    return {"motivation": "回应玩家", "mood": "social", "activities": [
        first, second or {"kind": "linger", "duration_s": 10, "motion_description": "A person stands calmly and listens."}],
        "ttl_s": 60, "interests": [], "avoid_targets": []}


class ChatPlayerRequestTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AutonomyTests()
        self.fixture.setUp()
        self.director = self.fixture.director
        self.clock = self.fixture.clock
        self.requests = []
        self.director._inspiration_callback = self.requests.append

    def tearDown(self):
        self.fixture.tearDown()

    def start_chat(self, text):
        self.assertTrue(self.director.note_player_message(2, text))
        self.assertTrue(self.director.begin_chat_engagement(2))
        return self.requests[-1]

    def test_latest_player_text_reaches_model_and_is_marked_handled(self):
        request = self.start_chat("挥挥手")
        context = request["context"]
        self.assertEqual(context["mode"], "chat_motion")
        self.assertEqual(context["chat_player_slot"], 2)
        self.assertEqual(context["latest_player_messages"][-1]["text"], "挥挥手")
        self.assertFalse(context["latest_player_messages"][-1]["handled"])
        value = validate_intent(_value({"kind": "linger", "duration_s": 6,
            "motion_description": "raises the right hand and waves it side to side toward the player"}), context)
        self.assertTrue(self.director.offer_intent(value, request["request_token"]))
        self.assertIn("waves", self.director.motion_intent_snapshot()["prompt"])
        self.director._request_intent("chat_motion_refresh")
        refreshed = self.requests[-1]["context"]["latest_player_messages"]
        self.assertTrue(refreshed[-1]["handled"])

    def test_old_messages_expire_and_text_is_bounded(self):
        self.director.note_player_message(2, "很久以前的话")
        self.clock.advance(1900)
        request = self.start_chat("你好" * 100)
        messages = request["context"]["latest_player_messages"]
        self.assertEqual(len(messages), 1)
        self.assertLessEqual(len(messages[0]["text"]), 144)

    def test_come_here_becomes_player_approach_and_pauses_refresh(self):
        self.fixture.session.players[2]["d"] = 4.0
        request = self.start_chat("走过来")
        value = validate_intent(_value({"kind": "socialize", "player_slot": 2, "duration_s": 10}), request["context"])
        self.assertTrue(self.director.offer_intent(value, request["request_token"]))
        motion = self.director.motion_intent_snapshot()
        self.assertEqual(motion["movement"], "approach_player")
        self.assertEqual(motion["player_slot"], 2)
        # 走过去期间周期刷新暂停，避免半路被原地动作替换。
        self.director._service_chat_engagement = lambda now: True
        self.director._update_active = lambda now: None
        self.director._startup_intent_needed = False
        count = len(self.requests)
        self.clock.advance(11)
        self.director._tick()
        self.assertEqual(len(self.requests), count)
        self.clock.advance(20)
        self.director._tick()
        self.assertEqual(self.requests[-1]["reason"], "chat_motion_refresh")

    def test_session_history_and_model_written_walk_text_are_used(self):
        for i in range(8):
            self.director.note_player_message(2, f"第{i}句")
            self.clock.advance(60)
        self.fixture.session.players[2]["d"] = 4.0
        request = self.start_chat("走过来")
        self.assertEqual(len(request["context"]["latest_player_messages"]), 9)
        walk = "A person hurries over with small light steps and stops in front of someone, smiling."
        value = validate_intent(_value({"kind": "socialize", "player_slot": 2, "duration_s": 10,
                                        "motion_description": walk}), request["context"])
        self.assertTrue(self.director.offer_intent(value, request["request_token"]))
        motion = self.director.motion_intent_snapshot()
        self.assertEqual(motion["movement"], "approach_player")
        self.assertEqual(motion["prompt"], walk)

    def test_chat_history_merges_yui_replies_and_world_messages_in_time_order(self):
        import json, tempfile, time as _time
        from pathlib import Path
        from yui_npc_controller.runtime.chat_context import RecentChatContextProvider
        from yui_npc_controller.runtime.config import YuiChatContextConfig
        from datetime import datetime
        root = Path(tempfile.mkdtemp())
        (root / "config").mkdir()
        (root / "config" / "characters.json").write_text(json.dumps({"当前猫娘": "YUI"}), encoding="utf-8")
        (root / "memory" / "YUI").mkdir(parents=True)
        old = datetime.fromtimestamp(_time.time() - 600).strftime("[%Y%m%d %a %H:%M]")
        (root / "memory" / "YUI" / "recent.json").write_text(json.dumps([
            {"type": "system", "data": {"content": "先前对话的备忘录: 不喜欢被摸头"}},
            {"type": "ai", "data": {"content": old + "行吧，挥就挥。"}},
            {"type": "ai", "data": {"content": [{"type": "text", "text": old + "别吵我。"}]}},
        ], ensure_ascii=False), encoding="utf-8")
        provider = RecentChatContextProvider(YuiChatContextConfig(enabled=True), runtime_root=root)
        provider.poll(force=True)
        self.assertEqual([m["role"] for m in provider.transcript()], ["memo", "yui", "yui"])
        self.assertEqual(provider.transcript()[1]["text"], "行吧，挥就挥。")
        self.director.chat_context_provider = provider
        request = self.start_chat("挥挥手")
        history = request["context"]["chat_history"]
        self.assertEqual([h["speaker"] for h in history], ["之前对话的备忘录", "YUI", "YUI", "玩家(slot 2)"])
        self.assertFalse(history[-1]["handled"])
        self.assertGreater(history[1]["minutes_ago"], 5)
        self.assertNotIn("recent_conversation", request["context"])

    def test_wave_as_in_place_never_walks_even_when_player_is_far(self):
        self.fixture.session.players[2]["d"] = 5.0
        request = self.start_chat("挥挥手")
        wave = "A person raises the right hand above the shoulder and waves it left and right several times."
        value = validate_intent(_value({"kind": "in_place", "duration_s": 6, "motion_description": wave}), request["context"])
        self.assertEqual(value["activities"][0]["kind"], "linger")
        self.assertTrue(self.director.offer_intent(value, request["request_token"]))
        motion = self.director.motion_intent_snapshot()
        self.assertEqual(motion["movement"], "blocked")
        self.assertEqual(motion["prompt"], wave)

    def test_walk_to_player_fills_speaker_slot_and_approaches(self):
        self.fixture.session.players[2]["d"] = 5.0
        request = self.start_chat("走过来")
        value = validate_intent(_value({"kind": "walk_to_player", "duration_s": 10,
                                        "motion_description": "A person walks forward at a normal pace and stops."}),
                                request["context"])
        self.assertEqual(value["activities"][0]["player_slot"], 2)
        self.assertTrue(self.director.offer_intent(value, request["request_token"]))
        self.assertEqual(self.director.motion_intent_snapshot()["movement"], "approach_player")

    def test_legacy_perform_with_description_becomes_in_place_instead_of_failing(self):
        request = self.start_chat("挥挥手")
        acts = request["context"]["catalog"] if "catalog" in request["context"] else None
        raw = {"kind": "perform", "action_key": "anything", "player_slot": 2, "duration_s": 6,
               "motion_description": "A person waves the right hand."}
        value = validate_intent(_value(raw), request["context"])
        self.assertEqual(value["activities"][0]["kind"], "linger")
        self.assertNotIn("action_key", value["activities"][0])

    def test_chat_schema_only_offers_two_kinds_and_requires_description(self):
        from yui_npc_controller.runtime.intent import _response_schema
        request = self.start_chat("挥挥手")
        schema = _response_schema(request["context"])
        item = schema["properties"]["activities"]["items"]
        self.assertEqual(item["properties"]["kind"]["enum"], ["in_place", "walk_to_player"])
        self.assertIn("motion_description", item["required"])
        self.assertNotIn("action_key", item["properties"])

    def test_chat_sequence_plays_each_item_for_its_duration_then_returns_to_talk(self):
        request = self.start_chat("挥挥手")
        wave = "A person raises the right hand and waves it left and right several times."
        arms = "A person crosses both arms over the chest."
        value = validate_intent({"motivation": "回应", "mood": "social", "activities": [
            {"kind": "in_place", "duration_s": 6, "motion_description": wave},
            {"kind": "in_place", "duration_s": 5, "motion_description": arms}],
            "ttl_s": 60, "interests": [], "avoid_targets": []}, request["context"])
        self.assertTrue(self.director.offer_intent(value, request["request_token"]))
        self.assertEqual(self.director.motion_intent_snapshot()["prompt"], wave)
        self.clock.advance(5.9)
        self.assertEqual(self.director.motion_intent_snapshot()["prompt"], wave)
        self.clock.advance(0.2)
        self.assertEqual(self.director.motion_intent_snapshot()["prompt"], arms)
        self.clock.advance(5.0)
        from yui_npc_controller.runtime.motion_intent import BASE_PROMPTS
        self.assertEqual(self.director.motion_intent_snapshot()["prompt"], BASE_PROMPTS["talk"])

    def test_periodic_refresh_waits_for_sequence_to_finish(self):
        request = self.start_chat("挥挥手")
        value = validate_intent({"motivation": "回应", "mood": "social", "activities": [
            {"kind": "in_place", "duration_s": 12, "motion_description": "A person waves the right hand."},
            {"kind": "in_place", "duration_s": 8, "motion_description": "A person nods twice."}],
            "ttl_s": 60, "interests": [], "avoid_targets": []}, request["context"])
        self.assertTrue(self.director.offer_intent(value, request["request_token"]))
        self.director._service_chat_engagement = lambda now: True
        self.director._update_active = lambda now: None
        self.director._startup_intent_needed = False
        count = len(self.requests)
        self.clock.advance(15)          # 超过 10 s 刷新周期，但序列（20 s）还没播完
        self.director._tick()
        self.assertEqual(len(self.requests), count)
        self.clock.advance(6)           # 序列播完
        self.director._tick()
        self.assertEqual(self.requests[-1]["reason"], "chat_motion_refresh")

    def test_come_here_when_already_close_only_turns_to_face(self):
        self.fixture.session.players[2]["d"] = 1.0
        request = self.start_chat("过来")
        value = validate_intent(_value({"kind": "socialize", "player_slot": 2, "duration_s": 10}), request["context"])
        self.assertTrue(self.director.offer_intent(value, request["request_token"]))
        self.assertEqual(self.director.motion_intent_snapshot()["movement"], "blocked")

    def test_chat_validation_still_rejects_navigation_and_foreign_targets(self):
        request = self.start_chat("去窗边")
        context = request["context"]
        for bad in ({"kind": "visit", "target_key": "a0", "duration_s": 10},
                    {"kind": "linger", "duration_s": 10, "player_slot": 2, "motion_description": "wave"},
                    {"kind": "socialize", "duration_s": 10}):
            with self.assertRaises(IntentModelError):
                validate_intent(_value(bad), context)


def test_player_target_is_encoded_in_reserved_byte_only_for_path_requests():
    packet = encode_stream_control(10, 1, 1, 1, "a" * 32, player_slot=3)
    assert packet[16] == 127 and packet[67] == 4
    assert encode_stream_control(10, 1, 1, 1, "a" * 32, anchor_id=5)[67] == 0
    import pytest
    for kwargs in ({"player_slot": 64}, {"player_slot": 1, "anchor_id": 2}):
        with pytest.raises(ValueError):
            encode_stream_control(10, 1, 1, 1, "a" * 32, **kwargs)
    with pytest.raises(ValueError):
        encode_stream_control(8, 1, 1, 1, "a" * 32, first_sequence=1, intent_version=1, player_slot=1)


def test_continuous_player_path_requires_prepared_flag_and_known_slot():
    from yui_npc_controller.runtime.motion_space import MotionSpace
    calls = []
    session = SimpleNamespace(last_log_sequence=0, add_event_listener=lambda cb: None,
                              _target_anchor_id=lambda key: 1, capabilities=[], players={0: {"slot": 0}})
    driver = ContinuousExecution(None, None, session)
    driver.ready = lambda s: True
    driver.space = MotionSpace(origin=[0, 0, 0], yaw=0, scale=1)
    driver._control = lambda *a, **k: calls.append(k) or dict(points=[[0, 0, 0], [0, 0, 2]], request_seq=1)
    driver._request = lambda path, **k: k
    assert driver.perform(session, prompt="walk", player_slot=0)["error"] == "player_path_unsupported"
    driver.player_path_supported = True
    assert driver.perform(session, prompt="walk", player_slot=5)["error"] == "slot_unknown"
    result = driver.perform(session, prompt="walk", player_slot=0, source="base", mode="path", end_condition="arrived")
    assert calls[-1] == {"player_slot": 0}
    assert result["plan"]["mode"] == "path" and result["duration_s"] > 2


def test_backend_routes_approach_intent_to_player_path_and_falls_back_on_rejection():
    backend = MotionBackend(MotionBackendConfig(enabled=True))
    performed = []

    class Continuous:
        def ready(self, session):
            return True

        def perform(self, session, **kwargs):
            performed.append(kwargs)
            return {"status": "failed", "error": "already_near"} if "player_slot" in kwargs else {"status": "accepted"}

    backend._continuous = Continuous()
    intent = {"version": 1, "prompt": "A person walks toward someone.", "movement": "approach_player",
              "player_slot": 0, "target_key": None, "fallback_prompt": "talk"}
    backend.update_base_intent(SimpleNamespace(), intent)
    assert performed[0]["player_slot"] == 0 and performed[0]["mode"] == "path"
    assert performed[1]["mode"] == "idle" and performed[1]["prompt"] == "talk"
    backend.update_base_intent(SimpleNamespace(), intent)
    assert len(performed) == 2  # 同一意图不重复下发


if __name__ == "__main__":
    unittest.main()
