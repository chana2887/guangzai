"""Receive ESP32 telemetry. Motion decisions stay on the microcontroller."""

import re
import threading
import time
import uuid

FRAME = re.compile(rb"GZ1,(0|[1-9][0-9]{0,9}),([NLRB]),([CLR])")
ZONE_NAMES = {b"N": "none", b"L": "left", b"R": "right", b"B": "both"}
TARGET_NAMES = {b"C": "center", b"L": "left", b"R": "right"}
HEARTBEAT_TIMEOUT = 1.5


class HardwareBridge:
    def __init__(self, port=""):
        self.port = port
        self.bridge_id = uuid.uuid4().hex
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.error = ""
        self.frame_at = None
        self.sequence = None
        self.zone = "none"
        self.target = "center"
        self.event = 0
        self.event_at = None
        self.sim_until = 0
        self.sim_armed_at = 0

    def start(self):
        if self.port:
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)

    def disconnected(self, error=""):
        with self.lock:
            self.frame_at = None
            self.sequence = None
            self.error = error

    def ingest(self, line, now=None):
        match = FRAME.fullmatch(line.strip())
        if not match:
            return False
        sequence = int(match[1])
        if sequence > 0xFFFFFFFF:
            return False
        target = TARGET_NAMES[match[3]]
        if sequence and target == "center":
            return False
        now = time.monotonic() if now is None else now
        with self.lock:
            fresh = self.frame_at is not None and now - self.frame_at < HEARTBEAT_TIMEOUT
            if fresh and sequence != self.sequence and target != "center":
                advanced = (sequence - self.sequence) & 0xFFFFFFFF
                if 0 < advanced < 2**31:
                    self.event += 1
                    self.event_at = now
            self.sequence = sequence
            self.zone = ZONE_NAMES[match[2]]
            self.target = target
            self.frame_at = now
            self.error = ""
        return True

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            connected = self.frame_at is not None and now - self.frame_at < HEARTBEAT_TIMEOUT
            sim = not self.port
            return {
                "mode": "sim" if sim else "serial",
                "connected": connected,
                "bridgeId": self.bridge_id,
                "zone": self.zone if connected or (sim and now < self.sim_until) else "none",
                "target": self.target if sim or connected else None,
                "event": self.event,
                "eventAgeMs": (
                    round((now - self.event_at) * 1000) if (sim or connected) and self.event_at is not None
                    else None
                ),
                "error": self.error,
            }

    def simulate(self, zone, now=None):
        """Generate a short tabletop demonstration pulse in simulator mode."""
        if self.port:
            raise ValueError("serial mode is active")
        if zone not in {"left", "right", "both"}:
            raise ValueError("unknown zone")
        now = time.monotonic() if now is None else now
        with self.lock:
            accepted = zone != "both" and now >= self.sim_armed_at
            self.zone = zone
            self.sim_until = now + 0.42
            self.sim_armed_at = max(self.sim_armed_at, now + (0.87 if accepted else 0.56))
            if accepted:
                self.target = zone
                self.event += 1
                self.event_at = now
            return accepted

    def run(self):
        try:
            import serial
        except ImportError:
            self.disconnected("缺少 pyserial")
            return

        while not self.stop.is_set():
            try:
                connection = serial.Serial(baudrate=115200, timeout=0.2)
                connection.dtr = False
                connection.rts = False
                connection.port = self.port
                with connection:
                    self.read(connection)
            except (OSError, ValueError):
                self.disconnected("串口暂不可用")
            self.stop.wait(1.0)

    def read(self, connection):
        buffer = bytearray()
        too_long = False
        while not self.stop.is_set():
            for byte in connection.read(connection.in_waiting or 1):
                if byte == 10:  # newline delimits frames across USB reads
                    if not too_long:
                        self.ingest(bytes(buffer))
                    buffer.clear()
                    too_long = False
                elif not too_long:
                    if len(buffer) < 64:
                        buffer.append(byte)
                    else:
                        too_long = True
