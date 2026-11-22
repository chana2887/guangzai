import tempfile
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from random import Random

from companion import Companion
from conversation import learn
from hardware import HardwareBridge
from notebook import day_of
from pet import Pet


class Clock:
    time = 1_800_000_000.0

    def __call__(self):
        return self.time

    def step(self, delta):
        self.time += delta


class LocalProvider:
    enabled = False


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "pet.sqlite3"
        self.clock = Clock()
        self.hardware = HardwareBridge()
        self.pet = self.open()

    def open(self):
        return Companion(self.path, self.hardware, LocalProvider(), self.clock)

    def key(self, name):
        revision = self.pet.state()["menu"]["revision"]
        return self.pet.button(name, revision)["state"]

    def choose(self, label):
        state = self.pet.state()
        if not state["menu"]["choices"]:
            state = self.key("center")
        choices = state["menu"]["choices"]
        for _ in range((choices.index(label) - state["menu"]["selected"]) % len(choices)):
            self.key("right")
        return self.key("center")

    def set_xp(self, xp):
        with self.pet.notebook.transaction(write=True) as db:
            profile = self.pet.notebook.pet(db, self.clock())
            profile.xp = xp
            self.pet.notebook.save(db, profile)

    def test_growth_unlocks_are_recorded_once_and_enable_wave(self):
        self.set_xp(56)
        profile = self.pet.state()
        self.assertEqual(profile["appearance"], {"room": "plain", "outfit": "plain"})
        self.assertEqual(profile["growth"]["unlocked"], [])
        self.choose("换装")
        self.assertNotIn("围巾", self.pet.state()["menu"]["choices"])
        self.choose("返回")
        self.choose("返回")
        self.pet.interact("pat")
        self.assertIn("wave", self.pet.state()["growth"]["unlocked"])
        self.clock.step(5)
        self.pet = self.open()
        self.hardware.simulate("left")
        self.assertEqual(self.pet.state()["scene"], "look")
        self.clock.step(2)
        self.assertEqual(self.pet.state()["scene"], "wave")
        self.assertEqual(self.pet.state()["direction"], "left")
        unlocks = [row for row in self.pet.history("events") if row["kind"] == "unlock"]
        self.assertEqual(len(unlocks), 1)
        self.assertEqual(unlocks[0]["data"]["rewards"], ["wave", "scarf"])

    def test_appearance_is_independent_and_survives_restart(self):
        self.set_xp(180)
        with self.pet.notebook.transaction(write=True) as db:
            db.execute("UPDATE pet SET data = json_remove(data, '$.room', '$.outfit')")
        self.pet = self.open()
        self.assertEqual(self.pet.state()["appearance"], {"room": "plain", "outfit": "plain"})
        self.choose("换装")
        self.choose("围巾")
        self.choose("换场景")
        self.choose("书房")
        self.pet = self.open()
        self.assertEqual(self.pet.state()["appearance"], {"room": "study", "outfit": "scarf"})
        self.choose("换场景")
        self.choose("默认场景")
        self.assertEqual(self.pet.state()["appearance"], {"room": "plain", "outfit": "scarf"})
        self.choose("换装")
        self.choose("默认服装")
        self.assertEqual(self.pet.state()["appearance"], {"room": "plain", "outfit": "plain"})

    def test_grown_demo_pet_can_propose_study_with_three_buttons(self):
        self.set_xp(180)
        self.pet = Companion(self.path, self.hardware, LocalProvider(), self.clock, demo=True)
        self.clock.step(106)
        self.assertIn("一起自习", self.pet.state()["menu"]["choices"])
        state = self.choose("一起自习")
        self.assertEqual(state["focus"]["phase"], "work")
        self.assertEqual(state["focus"]["minutes"], 25)

    def test_menu_opens_navigates_and_confirms_once_across_clients(self):
        self.assertIn("menu", self.pet.state())
        self.assertEqual(self.key("center")["menu"]["selected"], 0)
        self.assertEqual(self.pet.history("events"), [])
        menu = self.key("right")["menu"]
        self.assertEqual(menu["choices"][menu["selected"]], "投喂")
        self.assertEqual(self.open().state()["menu"], menu)
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: self.pet.button("center", menu["revision"]), range(2)))
        self.assertEqual(sum(result["accepted"] for result in results), 1)
        self.assertEqual(self.pet.state()["scene"], "feed")
        self.assertEqual([event["kind"] for event in self.pet.history("events")], ["feed"])

    def test_play_prompt_confirm_decline_and_expiry(self):
        self.assertIn("menu", self.pet.state())
        for ending in ("decline", "expire", "accept"):
            self.key("left")
            self.key("right")
            self.key("right")
            menu = self.key("center")["menu"]
            self.assertEqual(menu["choices"], ["一起玩", "晚点吧"])
            if ending == "expire":
                self.clock.step(31)
                self.assertFalse(self.pet.button("center", menu["revision"])["accepted"])
            else:
                if ending == "decline":
                    self.key("left")
                state = self.key("center")
                self.assertTrue(state["message"])
                self.assertEqual(state["scene"] == "play", ending == "accept")
            self.assertFalse(self.pet.state()["menu"]["choices"])
        self.assertEqual([e["kind"] for e in self.pet.history("events")], ["play"])

    def test_care_persists_and_cooldowns_prevent_duplicate_growth(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda _: self.pet.interact("pat"), range(4)))
        self.assertEqual(sum(result["accepted"] for result in results), 1)
        self.assertEqual(self.open().state()["growth"]["xp"], 4)
        self.assertEqual(self.pet.history("events")[0]["kind"], "pat")
        self.clock.step(10)
        self.assertTrue(self.pet.interact("feed")["accepted"])
        self.assertFalse(self.pet.interact("feed")["accepted"])
        self.assertGreater(self.pet.state()["needs"][0]["value"], 90)
        self.pet.interact("sleep")
        self.assertFalse(self.pet.interact("play")["accepted"])
        self.clock.step(3600)
        self.assertGreater(self.open().state()["needs"][1]["value"], 90)

    def invite_to_play(self):
        for key in ("center", "right", "right", "center"):
            state = self.key(key)
        return state["menu"]["prompt"]

    def test_completed_play_is_remembered_once_after_restart(self):
        self.assertNotIn("上次", self.invite_to_play())
        self.key("center")
        self.assertFalse(any(row["kind"] == "play_done" for row in self.pet.history("events")))
        self.clock.step(4)
        with ThreadPoolExecutor(max_workers=2) as workers:
            state = workers.submit(self.pet.state)
            completion = workers.submit(self.pet.tick)
            state.result()
            completion.result()
        completed = [row for row in self.pet.history("events") if row["kind"] == "play_done"]
        self.assertEqual(len(completed), 1)
        self.assertIn("play_event_id", completed[0]["data"])

        self.pet = self.open()
        self.assertIn("上次一起玩", self.pet.chat("你还记得我们上次一起玩吗？")["reply"])
        self.assertIn("上次一起玩", self.invite_to_play())
        self.key("left")
        self.key("center")
        self.clock.step(4)
        self.assertEqual(len([row for row in self.pet.history("events") if row["kind"] == "play_done"]), 1)
        self.clock.step(6 * 60)
        self.assertIn("上次一起玩", self.pet.state()["menu"]["prompt"])

    def test_interrupted_play_and_decline_do_not_become_memories(self):
        self.invite_to_play()
        self.key("left")
        self.key("center")
        self.assertNotIn("上次", self.invite_to_play())
        self.key("center")
        self.pet = self.open()
        self.clock.step(4)
        self.pet.state()
        self.assertNotIn("上次", self.invite_to_play())
        self.assertFalse(any(row["kind"] == "play_done" for row in self.pet.history("events")))

    def test_name_greeting_and_math_use_the_expected_sources(self):
        class FakeModel:
            enabled = True

            def reply(self, text, view, memories, recent):
                if "泰勒展开" not in text:
                    raise AssertionError("a local greeting was sent to the model")
                return "e^x = 1 + x + x^2/2! + x^3/3! + …"

        self.pet.chat("以后叫我小王")
        self.pet.chat("以后叫我小棋")
        restarted = Companion(self.path, self.hardware, FakeModel(), self.clock)
        greeting = restarted.chat("你好")
        self.assertEqual(greeting["source"], "local")
        self.assertIn("小棋", greeting["state"]["message"])
        self.assertNotIn("小王", greeting["state"]["message"])
        answer = restarted.chat("e^x 在 x=0 处的泰勒展开是什么？")
        self.assertEqual(answer["source"], "ai")
        self.assertIn("x^2/2!", answer["state"]["message"])

    def test_focus_completion_crosses_restart_and_rewards_once(self):
        self.pet.interact("focus", 25)
        self.assertFalse(self.pet.interact("play")["accepted"])
        self.clock.step(25 * 60 + 20)
        restarted = self.open()
        status = restarted.state()
        self.assertEqual(status["focus"]["phase"], "break")
        self.assertEqual(status["growth"]["xp"], 50)
        self.assertEqual(status["today"][-1]["value"], 25)
        self.clock.step(310)
        self.assertEqual(restarted.state()["focus"]["phase"], "")
        self.assertEqual(restarted.state()["growth"]["xp"], 50)
        self.assertIn("陪你自习 25 分钟", restarted.history("diary")[0]["text"])

    def test_offline_recall_prefers_latest_fact_and_preserves_history(self):
        self.pet.chat("我叫小王")
        self.pet.chat("我喜欢薄荷茶，也喜欢摄影")
        self.assertIn("薄荷茶", self.pet.chat("你还记得我喜欢什么吗？")["reply"])
        self.pet.chat("我现在不喜欢薄荷茶了")
        self.pet.chat("以后叫我小棋吧")
        restarted = self.open()
        self.assertIn("不喜欢薄荷茶", restarted.chat("你还记得薄荷茶吗？")["reply"])
        self.assertIn("小棋", restarted.chat("我叫什么？")["reply"])
        self.assertIn("没记牢", restarted.chat("你还记得我养的海豚吗？")["reply"])
        history = restarted.history("facts", "薄荷茶", include_history=True)
        self.assertEqual([note["current"] for note in history], [1, 0])
        self.assertEqual(learn("我喜欢薄荷茶吗？"), [])
        self.assertEqual(learn("我叫了辆出租车"), [])

    def test_remote_fallback_uses_local_companion_reply(self):
        class UnreachableProvider:
            enabled = True

            def reply(self, *args):
                return None

        pet = Companion(self.path, self.hardware, UnreachableProvider(), self.clock)
        result = pet.chat("今天很难过")
        self.assertEqual(result["source"], "local")
        self.assertEqual(result["state"]["scene"], "sad")
        self.assertIn("不好受", result["reply"])

    def test_gesture_updates_every_view_once_and_respects_sleep(self):
        self.assertTrue(self.hardware.simulate("left"))
        first = self.pet.state()
        self.assertEqual(first["direction"], "left")
        self.assertEqual(first["scene"], "look")
        self.pet.state()
        self.assertEqual(len(self.pet.history("events")), 1)
        self.assertFalse(self.hardware.simulate("both"))
        self.assertEqual(self.pet.state()["hardware"]["target"], "left")
        self.pet.interact("sleep")
        self.hardware.sim_armed_at = 0
        self.assertTrue(self.hardware.simulate("right"))
        self.assertEqual(self.pet.state()["scene"], "sleep")
        self.assertEqual(self.pet.state()["hardware"]["target"], "right")

    def test_idle_check_in_is_timed_and_limited(self):
        self.clock.step(46)
        self.assertEqual(self.pet.state()["scene"], "bored")
        self.assertEqual(self.pet.state()["message"], "")
        for _ in range(3):
            self.clock.step(361)
            self.assertIn("桌边", self.pet.state()["message"])
            self.clock.step(9)
            self.assertEqual(self.pet.state()["message"], "")
        self.clock.step(361)
        self.assertEqual(self.pet.state()["message"], "")

    def test_demo_shows_motion_and_invitation_then_returns_to_idle(self):
        pet = Companion(self.path, self.hardware, LocalProvider(), self.clock, demo=True, rng=Random(8))
        self.clock.step(20)
        self.assertIn(pet.state()["scene"], {"idle_glance", "idle_lean", "idle_hop"})
        self.assertFalse(pet.state()["message"])
        self.clock.step(90)
        invitation = pet.state()
        self.assertTrue(invitation["message"] or invitation["menu"]["prompt"])
        self.clock.step(9)
        after = pet.state()
        self.assertFalse(after["message"])
        self.assertFalse(after["menu"]["choices"])
        self.assertIn(after["scene"], {"idle", "bored"})

    def test_personality_weights_and_drowsy_motion(self):
        affectionate = Pet.new(self.clock())
        playful = Pet.new(self.clock())
        affectionate.traits["affection"] = 75
        playful.traits["curiosity"] = 75

        def sample(profile):
            rng = Random(42)
            return Counter(profile.choose_idle_motion(rng) for _ in range(120))

        affectionate_motion = sample(affectionate)
        playful_motion = sample(playful)
        self.assertGreater(affectionate_motion["idle_lean"], playful_motion["idle_lean"])
        self.assertGreater(playful_motion["idle_hop"], affectionate_motion["idle_hop"])
        playful.energy = 20
        self.assertEqual(sample(playful), {"idle_glance": 120})

    def test_demo_invitation_uses_personality_and_rejects_expired_button(self):
        pet = Companion(self.path, self.hardware, LocalProvider(), self.clock, demo=True)
        with pet.notebook.transaction(write=True) as db:
            profile = pet.notebook.pet(db, self.clock())
            profile.traits["affection"] = 70
            pet.notebook.save(db, profile)
        self.clock.step(110)
        menu = pet.state()["menu"]
        self.assertEqual(menu["choices"], ["摸摸头", "晚点吧"])
        self.clock.step(31)
        self.assertFalse(pet.button("center", menu["revision"])["accepted"])
        self.assertFalse(pet.state()["menu"]["choices"])
        self.assertFalse(pet.state()["message"])

    def test_gesture_overrides_ambient_motion_and_delays_invitation(self):
        pet = Companion(self.path, self.hardware, LocalProvider(), self.clock, demo=True)
        self.clock.step(104)
        self.assertIn(pet.state()["scene"], {"idle_glance", "idle_lean", "idle_hop"})
        self.clock.step(1)
        self.assertTrue(self.hardware.simulate("left"))
        state = pet.state()
        self.assertEqual((state["scene"], state["direction"]), ("look", "left"))
        self.assertFalse(state["menu"]["choices"])
        self.clock.step(4)
        self.assertFalse(pet.state()["message"])

    def test_sleep_and_study_have_quieter_motion_without_invitations(self):
        pet = Companion(self.path, self.hardware, LocalProvider(), self.clock, demo=True)
        pet.interact("sleep")
        self.clock.step(20)
        self.assertEqual(pet.state()["scene"], "sleep")
        self.clock.step(90)
        state = pet.state()
        self.assertEqual(state["scene"], "sleep_shift")
        self.assertFalse(state["message"] or state["menu"]["prompt"])
        pet.interact("focus", 5)
        self.clock.step(20)
        self.assertEqual(pet.state()["scene"], "focus")
        self.clock.step(90)
        state = pet.state()
        self.assertEqual(state["scene"], "focus_stretch")
        self.assertFalse(state["message"] or state["menu"]["prompt"])

    def test_gesture_replaces_a_pending_invitation_but_leaves_user_menu_open(self):
        pet = Companion(self.path, self.hardware, LocalProvider(), self.clock, demo=True)
        with pet.notebook.transaction(write=True) as db:
            profile = pet.notebook.pet(db, self.clock())
            profile.traits["curiosity"] = 75
            pet.notebook.save(db, profile)
        self.clock.step(108)
        invite = pet.state()["menu"]
        self.assertEqual(invite["choices"], ["一起玩", "晚点吧"])
        self.assertTrue(self.hardware.simulate("right"))
        state = pet.state()
        self.assertEqual((state["scene"], state["direction"]), ("look", "right"))
        self.assertFalse(state["menu"]["choices"])
        self.assertFalse(pet.button("center", invite["revision"])["accepted"])
        opened = pet.button("center", state["menu"]["revision"])["state"]
        self.hardware.sim_armed_at = 0
        self.assertTrue(self.hardware.simulate("left"))
        self.assertEqual(pet.state()["menu"], opened["menu"])

    def test_demo_starts_again_after_restart_and_leaves_daily_limit_unchanged(self):
        with self.pet.notebook.transaction(write=True) as db:
            profile = self.pet.notebook.pet(db, self.clock())
            profile.nudge_day = day_of(self.clock())
            profile.nudge_count = 3
            self.pet.notebook.save(db, profile)
        for _ in range(2):
            demo = Companion(self.path, self.hardware, LocalProvider(), self.clock, demo=True)
            self.assertFalse(demo.state()["message"])
            self.clock.step(105)
            self.assertTrue(demo.state()["message"])
            self.clock.step(10)
        self.clock.step(361)
        self.assertFalse(self.open().state()["message"])
