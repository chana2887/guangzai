"""SQLite event log, durable pet state and versioned facts."""

import json
import re
import sqlite3
import unicodedata
from collections import Counter
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from pet import Pet

LABELS = {
    "pat": "摸摸头", "feed": "投喂", "play": "开始玩耍", "play_done": "完成玩耍",
    "sleep": "睡觉", "wake": "醒来",
    "focus_start": "开始自习", "focus_done": "完成自习", "focus_stop": "结束计时",
    "chat": "对话", "gesture": "挥手", "level": "成长",
}
FACT_LABELS = {"name": "称呼", "like": "偏好", "habit": "习惯", "plan": "计划", "note": "便签"}
DAILY_GOALS = {"pat": ("摸头", 2), "feed": ("投喂", 1), "chat": ("聊天", 2), "focus_done": ("自习分钟", 25)}

SCHEMA = """
CREATE TABLE IF NOT EXISTS pet (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY,
    time REAL NOT NULL,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS event_day ON event(day, id);
CREATE INDEX IF NOT EXISTS event_kind ON event(kind, id);
CREATE TABLE IF NOT EXISTS fact (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    display TEXT NOT NULL,
    updated_at REAL NOT NULL,
    current INTEGER NOT NULL CHECK (current IN (0, 1))
);
CREATE UNIQUE INDEX IF NOT EXISTS current_fact ON fact(key) WHERE current = 1;
"""


def tokens(text):
    spans = re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", text.casefold())
    result = set()
    for span in spans:
        if ord(span[0]) < 128:
            result.add(span)
        else:
            result.update(span)
            result.update(span[i:i + 2] for i in range(len(span) - 1))
    return result


def normalize(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if not c.isspace())


def day_of(timestamp):
    return date.fromtimestamp(timestamp).isoformat()


class Notebook:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def transaction(self, write=False):
        db = sqlite3.connect(self.path, timeout=4)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def pet(db, now):
        row = db.execute("SELECT data FROM pet WHERE id = 1").fetchone()
        return Pet(**json.loads(row["data"])) if row else Pet.new(now)

    @staticmethod
    def save(db, pet):
        db.execute("INSERT INTO pet VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                   (json.dumps(pet.export(), ensure_ascii=False),))

    @staticmethod
    def add_event(db, kind, text, now, **data):
        event = db.execute("INSERT INTO event(time, day, kind, text, data) VALUES (?, ?, ?, ?, ?)",
                           (now, day_of(now), kind, text, json.dumps(data, ensure_ascii=False)))
        return event.lastrowid

    @staticmethod
    def has_completed_play(db):
        return db.execute("SELECT 1 FROM event WHERE kind = 'play_done' LIMIT 1").fetchone() is not None

    @staticmethod
    def save_fact(db, fact, now):
        current = db.execute("SELECT id, value FROM fact WHERE key = ? AND current = 1",
                             (fact["key"],)).fetchone()
        if current and current["value"] == fact["value"]:
            db.execute("UPDATE fact SET updated_at = ? WHERE id = ?", (now, current["id"]))
            return
        if current:
            db.execute("UPDATE fact SET current = 0 WHERE id = ?", (current["id"],))
        db.execute("""
            INSERT INTO fact(key, kind, value, display, updated_at, current)
            VALUES (:key, :kind, :value, :display, :time, 1)
        """, {**fact, "time": now})

    @staticmethod
    def facts(db, include_history=False):
        clause = "" if include_history else "WHERE current = 1"
        rows = db.execute(f"SELECT * FROM fact {clause} ORDER BY updated_at DESC, id DESC")
        return [{**dict(row), "label": FACT_LABELS[row["kind"]]} for row in rows]

    @staticmethod
    def events(db, limit=100):
        sql = "SELECT * FROM event ORDER BY id DESC"
        rows = db.execute(sql + " LIMIT ?", (limit,)) if limit is not None else db.execute(sql)
        return [{
            **dict(row), "label": LABELS[row["kind"]], "data": json.loads(row["data"]),
        } for row in rows]

    @staticmethod
    def find(query, records, field, limit=20):
        query = query.strip().casefold()
        if not query:
            return list(records)[:limit]
        query_tokens = tokens(query)
        ranked = []
        for index, record in enumerate(records):
            content = record[field].casefold()
            if query in content:
                score = 12
            else:
                shared = query_tokens & tokens(content)
                score = sum(3 if len(token) > 1 else 0.5 for token in shared)
            if score >= 1:
                ranked.append((-score, index, record))
        ranked.sort()
        return [record for _, _, record in ranked[:limit]]

    @staticmethod
    def goal_progress(db, now):
        rows = db.execute("""
            SELECT kind, count(*) AS count,
                   sum(CASE WHEN kind = 'focus_done' THEN json_extract(data, '$.minutes') ELSE 0 END) AS minutes
            FROM event WHERE day = ? GROUP BY kind
        """, (day_of(now),)).fetchall()
        counts = {row["kind"]: row["minutes"] if row["kind"] == "focus_done" else row["count"] for row in rows}
        return [{"label": label, "value": counts.get(kind, 0), "target": target}
                for kind, (label, target) in DAILY_GOALS.items()]

    @staticmethod
    def diary(db):
        rows = db.execute("""
            SELECT day, kind, count(*) AS count,
                   sum(CASE WHEN kind = 'focus_done' THEN json_extract(data, '$.minutes') ELSE 0 END) AS minutes
            FROM event
            WHERE day IN (SELECT DISTINCT day FROM event ORDER BY day DESC LIMIT 30)
            GROUP BY day, kind ORDER BY day DESC
        """)
        days = {}
        for row in rows:
            counts = days.setdefault(row["day"], Counter())
            counts[row["kind"]] = row["minutes"] if row["kind"] == "focus_done" else row["count"]
        result = []
        for day, counts in days.items():
            parts = []
            for kind, line in [
                ("pat", "摸摸头 {} 次"), ("feed", "吃点心 {} 次"), ("play_done", "一起玩了 {} 次"),
                ("chat", "聊了 {} 句"), ("gesture", "打了 {} 次招呼"), ("focus_done", "陪你自习 {} 分钟"),
            ]:
                if counts[kind]:
                    parts.append(line.format(counts[kind]))
            if counts["level"]:
                parts.append("光崽长大了一点")
            if not parts:
                parts.append("光崽在桌上度过了安静的一天")
            result.append({"day": day, "text": "，".join(parts) + "。"})
        return result
