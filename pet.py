"""The companion's care, growth and animation rules."""

from dataclasses import asdict, dataclass, field


NEEDS = {"fullness": "饱腹", "energy": "精神", "company": "陪伴"}
TRAITS = {
    "affection": "亲人",
    "curiosity": "好奇",
    "energy": "活泼",
    "concentration": "专注",
}
MOODS = {
    "calm": "平静", "happy": "开心", "curious": "好奇", "bored": "发呆",
    "lonely": "想找人玩", "hungry": "有点饿", "tired": "有点困", "sleep": "睡着了",
    "focus": "陪你自习",
}
STAGES = [(0, "幼体"), (60, "幼崽"), (180, "成长期"), (400, "成熟期")]
COOLDOWNS = {"pat": 4, "feed": 6, "play": 20, "sleep": 1, "focus": 1}
SCENE_SECONDS = {"pat": 1.8, "feed": 2.8, "play": 3.2, "look": 1.8, "happy": 1.8, "sad": 2.1}
MAX_OFFLINE_SECONDS = 8 * 3600
MENU_TIMEOUT = 30
MENUS = {
    "activities": ("想一起做点什么？", (("pat", "摸摸头"), ("feed", "投喂"), ("invite", "玩耍"), ("back", "返回"))),
    "invite": ("要不要一起玩？", (("play", "一起玩"), ("later", "晚点吧"))),
}


def bounded(value):
    return max(0, min(100, value))


@dataclass
class Pet:
    updated_at: float
    last_interaction: float
    energy: float = 82.0
    fullness: float = 72.0
    company: float = 60.0
    joy: float = 52.0
    xp: int = 0
    bond: float = 0.0
    traits: dict = field(default_factory=lambda: dict.fromkeys(TRAITS, 50.0))
    cooldowns: dict = field(default_factory=dict)
    mode: str = "awake"
    focus_phase: str = ""
    focus_until: float = 0.0
    focus_minutes: int = 25
    scene: str = ""
    scene_until: float = 0.0
    direction: str = ""
    motion_until: float = 0.0
    message: str = ""
    message_until: float = 0.0
    last_nudge_at: float = 0.0
    nudge_day: str = ""
    nudge_count: int = 0
    menu_kind: str = ""
    menu_index: int = 0
    menu_revision: int = 0
    menu_until: float = 0.0
    play_event_id: int = 0

    @classmethod
    def new(cls, now):
        return cls(updated_at=now, last_interaction=now, last_nudge_at=now)

    def export(self):
        return asdict(self)

    def advance(self, now):
        if self.menu_kind and now >= self.menu_until:
            self.close_menu()
        if now <= self.updated_at:
            return []
        hours = min(now - self.updated_at, MAX_OFFLINE_SECONDS) / 3600
        self.updated_at = now
        self.fullness = bounded(self.fullness - hours * 3)
        self.energy = bounded(self.energy + hours * (34 if self.mode == "sleep" else -2.5))
        self.company = bounded(self.company - hours * 3.5)
        self.joy += (50 - self.joy) * min(1, hours * 1.5)
        completed = []
        if self.focus_phase == "work" and now >= self.focus_until:
            finished_at = self.focus_until
            completed.append(("focus_done", finished_at, self.focus_minutes))
            self.grow(self.focus_minutes * 2, 3, "concentration", 5)
            self.focus_phase = "break"
            self.focus_until += 300
            if now < self.focus_until:
                self.speak("自习时间到。我们一起伸个懒腰。", now)
        if self.focus_phase == "break" and now >= self.focus_until:
            self.focus_phase, self.focus_until = "", 0
        return completed

    def grow(self, xp, bond, trait, change):
        self.xp += xp
        self.bond = bounded(self.bond + bond)
        score = self.traits[trait]
        self.traits[trait] = bounded(score + change * (1 - score / 125))

    def mood(self, now):
        if self.mode == "sleep":
            return "sleep"
        if now < self.motion_until or (self.scene == "look" and now < self.scene_until):
            return "curious"
        if self.focus_phase == "work":
            return "focus"
        if self.fullness <= 30:
            return "hungry"
        if self.energy <= 28:
            return "tired"
        if now < self.scene_until and self.scene in ("pat", "feed", "play", "happy"):
            return "happy"
        if self.company <= 32:
            return "lonely"
        if now - self.last_interaction >= 45:
            return "bored"
        return "happy" if self.joy >= 65 else "calm"

    def animation(self, now):
        if self.mode == "sleep":
            return "sleep", ""
        if self.focus_phase == "work":
            return "focus", ""
        if now < self.scene_until:
            return self.scene, self.direction if self.scene == "look" else ""
        mood = self.mood(now)
        return ("bored" if mood in {"bored", "lonely", "tired"} else "idle"), ""

    def speak(self, text, now, seconds=8):
        self.message = text
        self.message_until = now + seconds

    def react(self, scene, now, seconds=None):
        self.play_event_id = 0
        self.scene = scene
        self.scene_until = now + (seconds or SCENE_SECONDS[scene])

    def play_is_due(self, now):
        return bool(self.play_event_id and now >= self.scene_until)

    def open_menu(self, kind, now):
        self.menu_kind = kind
        self.menu_index = 0
        self.message_until = 0
        self.refresh_menu(now)

    def refresh_menu(self, now):
        self.menu_revision += 1
        self.menu_until = now + MENU_TIMEOUT

    def close_menu(self):
        if self.menu_kind:
            self.menu_revision += 1
        self.menu_kind = ""
        self.menu_index = 0
        self.menu_until = 0

    def menu_view(self):
        prompt, options = MENUS.get(self.menu_kind, ("", ()))
        return {"revision": self.menu_revision, "prompt": prompt, "selected": self.menu_index,
                "choices": [label for _, label in options]}

    def press(self, key, now):
        """Advance one menu step and return a care action only on final confirmation."""
        self.last_interaction = now
        if not self.menu_kind:
            self.open_menu("activities", now)
        elif key == "center":
            action = MENUS[self.menu_kind][1][self.menu_index][0]
            if action != "invite":
                self.close_menu()
                if action == "later":
                    self.speak("好呀，你忙。我在这里。", now, 5)
                return action if action in COOLDOWNS else None
            self.open_menu("invite", now)
        else:
            offset = -1 if key == "left" else 1
            self.menu_index = (self.menu_index + offset) % len(MENUS[self.menu_kind][1])
            self.refresh_menu(now)
        return None

    def gesture(self, direction, now):
        self.last_interaction = now
        self.direction = direction
        self.motion_until = now + SCENE_SECONDS["look"]
        if self.mode != "sleep" and self.focus_phase != "work":
            self.react("look", now)
            self.speak(
                "左边有人挥手，我看到啦。" if direction == "left" else "嗨，右边有动静！",
                now, 3,
            )

    def care(self, action, now, minutes=25):
        """Return (accepted, event kind, line). Callers record accepted actions."""
        if action not in COOLDOWNS:
            return False, "", "这个动作还没学会。"
        if now - self.cooldowns.get(action, -100) < COOLDOWNS[action]:
            return False, "", "等我缓一缓。"
        if self.mode == "sleep" and action not in {"sleep", "focus"}:
            return False, "", "光崽正在睡觉，叫醒它就能继续玩。"
        if self.focus_phase == "work" and action not in {"sleep", "focus"}:
            return False, "", "我陪你自习呢，休息了再玩。"
        if action == "feed" and self.fullness >= 96:
            return False, "", "肚子已经圆圆的了，留着下一口吧。"
        if action == "play" and self.energy <= 15:
            return False, "", "有点累，给我睡一小会儿？"

        self.cooldowns[action] = now
        self.last_interaction = now
        if action == "sleep":
            self.play_event_id = 0
            self.scene_until = 0
            self.focus_phase, self.focus_until = "", 0
            if self.mode == "sleep":
                self.mode = "awake"
                return True, "wake", "醒啦！刚才梦到我会飞。"
            self.mode = "sleep"
            return True, "sleep", "我把眼睛闭一会儿，你忙你的。"

        if action == "focus":
            self.play_event_id = 0
            self.scene_until = 0
            self.mode = "awake"
            if self.focus_phase:
                self.focus_phase, self.focus_until = "", 0
                return True, "focus_stop", "好，计时暂停，我们休息一下。"
            self.focus_phase = "work"
            self.focus_minutes = minutes
            self.focus_until = now + minutes * 60
            return True, "focus_start", f"这 {minutes} 分钟，我在旁边陪你。"

        if action == "pat":
            self.company = bounded(self.company + 15)
            self.joy = bounded(self.joy + 12)
            self.grow(4, 2, "affection", 3)
            line = "嗯……这边也摸一下。" if self.bond >= 12 else "刚刚那下挺舒服，再轻一点点。"
        elif action == "feed":
            self.fullness = bounded(self.fullness + 26)
            self.joy = bounded(self.joy + 8)
            self.grow(6, 2, "energy", 2)
            line = "嚼完再说。嗯，好吃！"
        else:
            self.energy = bounded(self.energy - 12)
            self.fullness = bounded(self.fullness - 6)
            self.company = bounded(self.company + 22)
            self.joy = bounded(self.joy + 18)
            self.grow(8, 3, "curiosity", 4)
            line = "来比谁能先转回来！"
        self.react(action, now)
        return True, action, line

    def progress(self):
        index = max(i for i, (xp, _) in enumerate(STAGES) if self.xp >= xp)
        base, stage = STAGES[index]
        goal = STAGES[index + 1][0] if index + 1 < len(STAGES) else None
        return {
            "stage": stage, "xp": self.xp, "next": goal,
            "percent": round(100 * (self.xp - base) / (goal - base)) if goal else 100,
        }

    def view(self, now):
        scene, direction = self.animation(now)
        trait = max(self.traits, key=self.traits.get)
        return {
            "needs": [{"key": k, "label": label, "value": round(getattr(self, k))}
                      for k, label in NEEDS.items()],
            "traits": [{"label": label, "value": round(self.traits[k])} for k, label in TRAITS.items()],
            "personality": TRAITS[trait] if self.traits[trait] >= 55 else "性格养成中",
            "bond": round(self.bond),
            "growth": self.progress(),
            "mood": MOODS[self.mood(now)],
            "mode": self.mode,
            "scene": scene,
            "menu": self.menu_view(),
            "direction": direction,
            "message": self.message if now < self.message_until else "",
            "focus": {
                "phase": self.focus_phase,
                "remaining": max(0, round(self.focus_until - now)),
                "minutes": self.focus_minutes,
            },
        }
