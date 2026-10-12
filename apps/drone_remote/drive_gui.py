#!/usr/bin/env python3
"""방향키로 Pi 의 drive_server.py 에 duty 를 UDP 로 쏘는 Mac 측 GUI.

    uv run --no-project drive_gui.py --host 10.53.199.81

조작:  ↑↓←→ 주행 (동시 입력 가능) | Space 즉시 정지 | o/p 속도 -/+ | k/l 회전량 -/+

창에 포커스가 있을 때만 키를 받는다. 포커스를 잃으면 눌린 키를 모두 놓은 것으로 친다.
"""

from __future__ import annotations

import argparse
import socket
import struct
import tkinter as tk

# drive_server.py 와 반드시 같아야 한다.
PACKET = struct.Struct("<4sIhh")
MAGIC = b"DRV1"

SEND_PERIOD_MS = 50  # 20Hz. 서버 deadman(300ms) 보다 충분히 짧게.

# 키를 꾹 누르면 OS autorepeat 가 Release→Press 를 연달아 보낸다. Release 직후 이 시간 안에
# Press 가 다시 오면 "계속 누르고 있음" 으로 본다. 안 그러면 주행 중에 모터가 떨린다.
RELEASE_DEBOUNCE_MS = 60

DUTY_MAX = 1000
STEP = 50

# ── 배선에 맞춰 조정 ──────────────────────────────────────────────────────────
# ↑ 를 눌렀는데 한쪽이 뒤로 돌면 그쪽 INVERT, ← 를 눌렀는데 오른쪽으로 돌면 SWAP.
SWAP_AB = False  # False: A=왼쪽, B=오른쪽
INVERT_A = False
INVERT_B = False

ARROWS = ("Up", "Down", "Left", "Right")


def mix(throttle: int, steer: int, speed: int, turn: int) -> tuple[int, int]:
    """차동 구동 믹싱. throttle/steer 는 -1, 0, +1.

    제자리(throttle=0)면 좌우가 반대로 돌아 pivot 회전, 전후진 중이면 안쪽 바퀴가 느려진다.
    """
    left = throttle * speed + steer * turn
    right = throttle * speed - steer * turn
    clamp = lambda v: max(-DUTY_MAX, min(DUTY_MAX, v))
    return clamp(left), clamp(right)


class DriveApp:
    def __init__(self, root: tk.Tk, host: str, port: int):
        self.root = root
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.seq = 0

        self.pressed: set[str] = set()
        self._release_jobs: dict[str, str] = {}
        self.speed = 400
        self.turn = 300

        root.title(f"Drive → {host}:{port}")
        root.resizable(False, False)

        pad = tk.Frame(root, padx=16, pady=12)
        pad.pack()
        self.arrow_lbl: dict[str, tk.Label] = {}
        for key, sym, r, c in (
            ("Up", "▲", 0, 1),
            ("Left", "◀", 1, 0),
            ("Down", "▼", 1, 1),
            ("Right", "▶", 1, 2),
        ):
            lbl = tk.Label(
                pad,
                text=sym,
                width=4,
                height=2,
                font=("Helvetica", 22),
                relief="ridge",
                bg="#dddddd",
            )
            lbl.grid(row=r, column=c, padx=3, pady=3)
            self.arrow_lbl[key] = lbl

        self.info = tk.Label(root, font=("Menlo", 13), justify="left", padx=16, pady=8)
        self.info.pack()
        tk.Label(root, text="Space 정지 · o/p 속도 · k/l 회전", fg="#666").pack(
            pady=(0, 10)
        )

        root.bind("<KeyPress>", self.on_press)
        root.bind("<KeyRelease>", self.on_release)
        root.bind("<FocusOut>", lambda _e: self.release_all())
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.focus_force()

        self.tick()

    # ── 키 입력 ──────────────────────────────────────────────────────────────
    def on_press(self, e: tk.Event) -> None:
        k = e.keysym
        if k in ARROWS:
            job = self._release_jobs.pop(k, None)
            if job:
                self.root.after_cancel(job)  # autorepeat 의 가짜 Release 취소
            self.pressed.add(k)
        elif k == "space":
            self.release_all()
        # 한글 입력 상태면 o/p/k/l 이 ㅐ/ㅔ/ㅏ/ㅣ 로 들어온다. Caps Lock 대문자도 같이 받는다.
        elif e.char in ("o", "O", "ㅐ"):
            self.speed = max(0, self.speed - STEP)
        elif e.char in ("p", "P", "ㅔ"):
            self.speed = min(DUTY_MAX, self.speed + STEP)
        elif e.char in ("k", "K", "ㅏ"):
            self.turn = max(0, self.turn - STEP)
        elif e.char in ("l", "L", "ㅣ"):
            self.turn = min(DUTY_MAX, self.turn + STEP)
        else:
            print(f"unmapped key: keysym={k!r} char={e.char!r}")  # 안 먹는 키 진단용

    def on_release(self, e: tk.Event) -> None:
        k = e.keysym
        if k in ARROWS and k not in self._release_jobs:
            self._release_jobs[k] = self.root.after(
                RELEASE_DEBOUNCE_MS, self._really_release, k
            )

    def _really_release(self, k: str) -> None:
        self._release_jobs.pop(k, None)
        self.pressed.discard(k)

    def release_all(self) -> None:
        for job in self._release_jobs.values():
            self.root.after_cancel(job)
        self._release_jobs.clear()
        self.pressed.clear()

    # ── 송신 루프 ────────────────────────────────────────────────────────────
    def target(self) -> tuple[int, int]:
        p = self.pressed
        throttle = ("Up" in p) - ("Down" in p)
        steer = ("Right" in p) - ("Left" in p)
        left, right = mix(throttle, steer, self.speed, self.turn)
        a, b = (right, left) if SWAP_AB else (left, right)
        return (-a if INVERT_A else a), (-b if INVERT_B else b)

    def send(self, a: int, b: int) -> None:
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        try:
            self.sock.sendto(PACKET.pack(MAGIC, self.seq, a, b), self.addr)
        except OSError as e:
            # 네트워크가 잠깐 없어도 GUI 는 살려 둔다. 서버 deadman 이 모터를 세운다.
            self.info.config(text=f"send error: {e}")

    def tick(self) -> None:
        a, b = self.target()
        self.send(a, b)
        for k, lbl in self.arrow_lbl.items():
            lbl.config(bg="#4caf50" if k in self.pressed else "#dddddd")
        self.info.config(
            text=f"A={a:+5d}  B={b:+5d}\nspeed={self.speed}  turn={self.turn}"
        )
        self.root.after(SEND_PERIOD_MS, self.tick)

    def on_close(self) -> None:
        # 정지 패킷을 몇 번 확실히 쏘고 닫는다. 유실돼도 서버 deadman 이 받쳐준다.
        for _ in range(3):
            self.send(0, 0)
        self.root.destroy()


def main() -> None:
    p = argparse.ArgumentParser(description="방향키 → UDP 드라이브 GUI")
    p.add_argument("--host", default="10.53.199.81")
    p.add_argument("--port", type=int, default=9000)
    a = p.parse_args()

    root = tk.Tk()
    DriveApp(root, a.host, a.port)
    root.mainloop()


if __name__ == "__main__":
    main()
