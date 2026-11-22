"""Coordinates care, serial motion, SQLite and dialogue for all display clients."""

import time
from threading import Lock

from conversation import GREETING, PLAY_RECALL, RECALL, SAD, ChatProvider, learn, offline_reply
from notebook import Notebook, day_of
from pet import SCENE_SECONDS

ACTIONS = {"pat", "feed", "play", "sleep", "focus"}
FOCUS_MINUTES = {5, 15, 25, 45}
NUDGE_SECONDS = 6 * 60


class Companion:
    def __init__(self, path, hardware, provider=None, clock=time.time):
        self.notebook = Notebook(path)
        self.hardware = hardware
        self.provider = provider or ChatProvider()
        self.clock = clock
        self.chat_lock = Lock()
        self.gesture_lock = Lock()
        self.last_event = 0
        with self.notebook.transaction(write=True) as db:
            now = self.clock()
            pet = self.notebook.pet(db, now)
            if pet.play_event_id:
                pet.play_event_id = 0
                pet.scene_until = pet.message_until = 0
            self.advance(db, pet, now)
            self.notebook.save(db, pet)

    def milestone(self, db, pet, previous, now):
        stage = pet.progress()["stage"]
        if stage != previous:
            self.notebook.add_event(db, "level", f"光崽成长为{stage}。", now)

    def advance(self, db, pet, now):
        if pet.play_is_due(now):
            self.notebook.add_event(db, "play_done", "和光崽一起玩完了一轮。", pet.scene_until,
                                    play_event_id=pet.play_event_id)
            pet.play_event_id = 0
        previous = pet.progress()["stage"]
        for kind, finished_at, minutes in pet.advance(now):
            self.notebook.add_event(db, kind, f"一起自习了 {minutes} 分钟。", finished_at,
                                    minutes=minutes)
            self.milestone(db, pet, previous, finished_at)

    def tick(self):
        """Finish play even while no browser is polling."""
        now = self.clock()
        with self.notebook.transaction() as db:
            if not self.notebook.pet(db, now).play_is_due(now):
                return
        with self.notebook.transaction(write=True) as db:
            pet = self.notebook.pet(db, now)
            if pet.play_is_due(now):
                self.advance(db, pet, now)
                self.notebook.save(db, pet)

    def receive_gesture(self, db, pet, report, now):
        direction = report["target"]
        if report["eventAgeMs"] is None or report["eventAgeMs"] >= 1500 or direction not in {"left", "right"}:
            return
        event_time = now - report["eventAgeMs"] / 1000
        if event_time >= pet.last_interaction:
            pet.gesture(direction, event_time)
        label = "左侧" if direction == "left" else "右侧"
        self.notebook.add_event(db, "gesture", f"有人从{label}和光崽打招呼。", event_time,
                                direction=direction, source=report["mode"])

    @staticmethod
    def can_nudge(pet, now):
        return (pet.mode == "awake" and not pet.menu_kind and pet.focus_phase != "work" and pet.scene_until <= now and
                now - max(pet.last_interaction, pet.last_nudge_at) >= NUDGE_SECONDS and
                (pet.nudge_day != day_of(now) or pet.nudge_count < 3))

    def nudge(self, db, pet, now):
        if not self.can_nudge(pet, now):
            return False
        mood = pet.mood(now)
        lines = {
            "hungry": "等你忙完，可以给我留一小口点心。",
            "tired": "我有点困了，眯会儿再一起玩吧。",
            "lonely": "想歇一会儿的话，可以来摸摸我的头。",
            "bored": "我在桌边待着。忙完了叫我一声。",
        }
        if mood not in lines:
            return False
        if mood in {"lonely", "bored"} and self.notebook.has_completed_play(db):
            pet.open_menu("invite", now)
        else:
            pet.speak(lines[mood], now)
        pet.last_nudge_at = now
        day = day_of(now)
        if pet.nudge_day != day:
            pet.nudge_day = day
            pet.nudge_count = 0
        pet.nudge_count += 1
        return True

    def view(self, db, pet, report, now):
        view = pet.view(now)
        if pet.menu_kind == "invite" and self.notebook.has_completed_play(db):
            view["menu"]["prompt"] = "上次一起玩得很开心。今天还玩吗？"
        return {
            **view,
            "serverTime": now,
            "hardware": report,
            "today": self.notebook.goal_progress(db, now),
            "aiAvailable": self.provider.enabled,
        }

    def state_needs_write(self, pet, new_gesture, now):
        return (new_gesture or (pet.focus_phase and now >= pet.focus_until) or
                pet.play_is_due(now) or (pet.menu_kind and now >= pet.menu_until) or
                now - pet.updated_at >= 300 or self.can_nudge(pet, now))

    def state(self):
        with self.gesture_lock:
            report = self.hardware.snapshot()
            new_gesture = report["event"] > self.last_event
            now = self.clock()
            with self.notebook.transaction() as db:
                pet = self.notebook.pet(db, now)
                if not self.state_needs_write(pet, new_gesture, now):
                    pet.advance(now)
                    return self.view(db, pet, report, now)
            with self.notebook.transaction(write=True) as db:
                now = self.clock()
                pet = self.notebook.pet(db, now)
                self.advance(db, pet, now)
                if new_gesture:
                    self.receive_gesture(db, pet, report, now)
                self.nudge(db, pet, now)
                self.notebook.save(db, pet)
                result = self.view(db, pet, report, now)
            self.last_event = report["event"]
            return result

    def apply_care(self, db, pet, action, now, minutes=25, dialogue_revision=None):
        stage = pet.progress()["stage"]
        accepted, kind, message = pet.care(action, now, minutes)
        if accepted:
            if kind == "play":
                pet.play_event_id = self.notebook.add_event(
                    db, kind, message, now, dialogue_revision=dialogue_revision,
                )
            else:
                self.notebook.add_event(db, kind, message, now)
            self.milestone(db, pet, stage, now)
        pet.speak(message, now, 5)
        return accepted

    def button(self, key, revision):
        with self.notebook.transaction(write=True) as db:
            now = self.clock()
            pet = self.notebook.pet(db, now)
            self.advance(db, pet, now)
            accepted = revision == pet.menu_revision
            if accepted:
                action = pet.press(key, now)
                if action:
                    self.apply_care(db, pet, action, now, dialogue_revision=revision)
            self.notebook.save(db, pet)
            result = self.view(db, pet, self.hardware.snapshot(), now)
        return {"accepted": accepted, "state": result}

    def interact(self, action, minutes=25):
        with self.notebook.transaction(write=True) as db:
            now = self.clock()
            pet = self.notebook.pet(db, now)
            self.advance(db, pet, now)
            pet.close_menu()
            accepted = self.apply_care(db, pet, action, now, minutes)
            self.notebook.save(db, pet)
            result = self.view(db, pet, self.hardware.snapshot(), now)
        return {"accepted": accepted, "state": result}

    def chat(self, text):
        if not self.chat_lock.acquire(blocking=False):
            return None
        try:
            assertions = learn(text)
            # Explicit facts, recalls and brief greetings also work offline.
            play_recall = PLAY_RECALL.search(text)
            use_provider = self.provider.enabled and not (
                assertions or RECALL.search(text) or play_recall or GREETING.fullmatch(text)
            )
            with self.notebook.transaction(write=True) as db:
                now = self.clock()
                pet = self.notebook.pet(db, now)
                self.advance(db, pet, now)
                if use_provider and not pet.play_event_id and pet.mode == "awake" and pet.focus_phase != "work":
                    pet.react("think", now, 10)
                pet.last_interaction = now
                facts = self.notebook.facts(db)
                played_before = bool(play_recall and self.notebook.has_completed_play(db))
                pet.close_menu()
                recent = [event for event in self.notebook.events(db, 100) if event["kind"] == "chat"][:4]
                self.notebook.save(db, pet)
            relevant = self.notebook.find(text, facts, "display", limit=5)
            chosen = relevant + [item for item in facts if item not in relevant][:3]
            remote = self.provider.reply(text, pet.view(now), chosen, recent) if use_provider else None
            reply = remote or offline_reply(text, assertions, pet, facts, recent, played_before)
            source = "ai" if remote else "local"
            with self.notebook.transaction(write=True) as db:
                now = self.clock()
                pet = self.notebook.pet(db, now)
                self.advance(db, pet, now)
                stage = pet.progress()["stage"]
                pet.grow(3, 1.5, "affection", 1.5)
                pet.company = min(100, pet.company + 8)
                pet.last_interaction = now
                if pet.mode == "awake" and pet.focus_phase != "work" and (
                    pet.scene_until < now or pet.scene == "think"
                ):
                    scene = "sad" if SAD.search(text) else "happy"
                    pet.react(scene, now, SCENE_SECONDS[scene])
                pet.speak(reply, now, seconds=max(8, min(20, len(reply) / 6)))
                for learned in assertions:
                    self.notebook.save_fact(db, learned, now)
                self.notebook.add_event(db, "chat", text, now, reply=reply, source=source)
                self.milestone(db, pet, stage, now)
                self.notebook.save(db, pet)
                view = self.view(db, pet, self.hardware.snapshot(), now)
            return {"reply": reply, "source": source, "state": view}
        finally:
            self.chat_lock.release()

    def history(self, collection, query="", include_history=False):
        with self.notebook.transaction() as db:
            if collection == "facts":
                records = self.notebook.facts(db, include_history)
                return self.notebook.find(query, records, "display", 60)
            if collection == "diary":
                return self.notebook.find(query, self.notebook.diary(db), "text", 30)
            records = self.notebook.events(db, None if query else 100)
            return self.notebook.find(query, records, "text", 60)

    def export(self):
        with self.notebook.transaction() as db:
            return {
                "version": 1,
                "exportedAt": self.clock(),
                "pet": self.notebook.pet(db, self.clock()).export(),
                "facts": self.notebook.facts(db, include_history=True),
                "events": self.notebook.events(db, limit=None),
            }
