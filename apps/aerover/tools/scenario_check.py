"""시나리오 점검 — 실제 메인 창을 띄워 사용자 흐름대로 조작하고 단계마다 검사한다.

    uv run python -m tools.scenario_check                    # 약 3분, 창이 뜬다
    uv run python -m tools.scenario_check --shots /tmp/sc    # 단계별 화면 캡처까지 (macOS 화면 녹화 권한)

가짜 파이(`tools/fake_pi.py`)를 **이 스크립트가 띄우고 죽인다.** 포트는 14650 / 5104 —
손으로 띄운 가짜 파이 · 앱(14550 / 5004)과 겹치지 않는다. 탐지 페이지의 시나리오:

    S1 링크 없음 안내     S2 상태 페이지에서 연결   S3 탐지 → 자세 · 핀 · PiP · 드론
    S4 핀 호버 · PiP 클릭  S5 신뢰도 변경          S6 탐지 중 맵 바꾸기
    S7 페이지 왕복        S8 정지(드론 지움) · 재시작   S9 파이가 죽음 → 자동 정지
    S10 재연결 · 재탐지   S11 탐지 중 창 닫기

**도는 동안 마우스 · 키보드를 건드리지 않는다.** S4 는 실제 OS 커서를 핀 위로 옮겨 호버를 본다 —
다른 창(Spotlight 등)이 앞에 오면 이벤트가 그쪽으로 가서 실패한다 (그때는 실패 줄에 "앱 창이 맨 앞 아님").

Qt 슬롯 안의 예외는 앱을 죽이지 않고 흘러가 버리므로 예외 훅으로 모아 따로 보인다.
실패나 예외가 하나라도 있으면 종료 코드 1. 장비 · NodeODM · 매핑 페이지 흐름은 다루지 않는다.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

RUN = "20261004_130739"         # 사진 · 맵 · 폰 K 가 모두 있는 회차
PORT, RTP = 14650, 5104

# 설정(src/config)은 import 할 때 환경변수를 읽는다 — 그 전에 정한다.
_ROOT = Path(__file__).resolve().parents[1]
os.environ["AEROVER_RTP_PORT"] = str(RTP)
os.environ.setdefault("AEROVER_CAMERA", str(_ROOT / "3D_model" / RUN / "replay_camera.json"))

from PySide6.QtCore import QPoint, QSettings   # noqa: E402
from PySide6.QtGui import QCursor               # noqa: E402
from PySide6.QtWidgets import QApplication      # noqa: E402

from src.config import ROOT, SESSIONS_DIR       # noqa: E402
from src.gl.viewport import configure_surface_format   # noqa: E402


class Checker:
    """단계 결과 · 예외 · 캡처를 모은다."""

    def __init__(self, app: QApplication, shots: Path | None) -> None:
        self.app, self.shots = app, shots
        self.results: list[tuple[str, bool, str]] = []
        self.errors: list[str] = []
        sys.excepthook = lambda t, v, tb: self.errors.append("".join(traceback.format_exception(t, v, tb)))

    def pump(self, sec: float) -> None:
        end = time.monotonic() + sec
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def wait(self, cond, sec: float) -> bool:
        end = time.monotonic() + sec
        while time.monotonic() < end:
            self.app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def check(self, name: str, ok: bool, info: str = "") -> None:
        self.results.append((name, bool(ok), info))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {info}", flush=True)

    def shot(self, win, name: str) -> None:
        if self.shots is None:
            return
        g = win.frameGeometry()
        subprocess.run(["screencapture", "-x", "-R", f"{g.x()},{g.y()},{g.width()},{g.height()}",
                        str(self.shots / f"{name}.png")])


def plain(label) -> str:
    return re.sub("<[^>]+>", "", label.text()).replace("&nbsp;", " ")


def start_pi() -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, "-m", "tools.fake_pi", str(SESSIONS_DIR / RUN), "--fps", "5",
                          "--start", "100", "--port", str(PORT), "--rtp-port", str(RTP)],
                         cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.5)
    return p


def stop_pi(p: subprocess.Popen) -> None:
    p.kill()
    p.wait()


def scenario(c: Checker, app: QApplication) -> None:
    from src.main_window import MainWindow
    from src.pages.detect import MODE_3D, MODE_LIVE, NO_LINK

    win = MainWindow()
    scr = app.primaryScreen().availableGeometry()
    win.setGeometry(scr.x() + 20, scr.y() + 40, min(1440, scr.width() - 40), min(900, scr.height() - 60))
    win.show(); win.raise_(); win.activateWindow()
    c.pump(1.5)
    status, _mapping, detect = (win.stack.widget(i) for i in range(3))
    nav = win._nav_group.buttons()
    pi = None
    try:
        # ---- S1 실행 직후: 링크 없음 ----
        nav[2].click(); c.pump(1.5)
        c.check("S1 탐지 버튼은 링크 없이 잠김", not detect.detect_btn.isEnabled(), detect.detect_note.text())
        c.check("S1 맵은 저장된 회차로 열림", detect.map_box.currentText() == RUN, detect.map_box.currentText())
        c.check("S1 PiP 는 연결 안내", NO_LINK in detect.pip.label.text(), detect.pip.label.text())
        c.check("S1 격자 라벨 8개", len(detect.viewport._grid_labels) == 8)
        detect._modes.button(MODE_LIVE).click(); c.pump(0.3)
        c.check("S1 실시간 모드도 연결 안내", NO_LINK in detect.video.label.text() and detect.pip.isHidden())
        detect._modes.button(MODE_3D).click(); c.pump(0.3)
        c.shot(win, "s1_idle")

        # ---- S2 상태 페이지에서 연결 ----
        pi = start_pi()
        nav[0].click(); c.pump(0.5)
        status.addr.setText(f"127.0.0.1:{PORT}")
        status.connect_btn.click()
        c.check("S2 링크 연결", c.wait(lambda: win.hub.active and status.capture_btn.isEnabled(), 10),
                status.note.text())
        c.shot(win, "s2_status")

        # ---- S3 탐지 시작 ----
        nav[2].click(); c.pump(0.5)
        c.check("S3 탐지 버튼 열림", detect.detect_btn.isEnabled(), detect.detect_note.text())
        detect.detect_btn.click()
        c.check("S3 모델 열림(정지 버튼으로)", c.wait(lambda: detect.detect_btn.text() == "탐지 정지", 5))
        c.check("S3 카메라 자세 수신", c.wait(lambda: detect._camera is not None, 30), detect.detect_note.text())
        c.check("S3 핀이 생김", c.wait(lambda: len(detect._shown) > 0, 40), plain(detect.summary))
        c.pump(10)
        c.check("S3 PiP 에 영상", detect.pip._image is not None)
        c.check("S3 드론 메시로 그림",
                detect.viewport._drone_model is not None and detect.viewport._drone_part is not None)
        c.shot(win, "s3_detect")

        # ---- S4 핀 호버 → 실시간 전환 → 복귀 (실제 OS 커서) ----
        vp = detect.viewport
        pin = vp.marker_pos(0)
        c.check("S4 핀 화면 좌표", pin is not None)
        if pin is not None:
            QCursor.setPos(vp.mapToGlobal(pin.toPoint()) + QPoint(40, 40)); c.pump(0.3)
            QCursor.setPos(vp.mapToGlobal(pin.toPoint())); c.pump(0.8)
            front = "" if app.activeWindow() is win else " (앱 창이 맨 앞 아님 — 점검 중 다른 창이 떴다)"
            c.check("S4 호버 카드 뜸", detect.hover_card.isVisible(), plain(detect.hover_text)[:60] + front)
            c.shot(win, "s4_hover")
        detect.pip.clicked.emit(); c.pump(1.5)
        c.check("S4 PiP 클릭 → 실시간", detect.views.currentIndex() == MODE_LIVE and detect.pip.isHidden()
                and not detect.hover_card.isVisible())
        c.check("S4 실시간 화면에 영상", detect.video._image is not None)
        c.shot(win, "s4_live")
        detect._modes.button(MODE_3D).click(); c.pump(1.0)
        c.check("S4 3D 복귀 시 PiP 다시 보임", not detect.pip.isHidden() and detect.views.currentIndex() == MODE_3D)
        QCursor.setPos(QPoint(scr.x() + 5, scr.y() + 5)); c.pump(0.3)

        # ---- S5 신뢰도 바꾸기 (실행 중) ----
        detect.conf.setValue(80); c.pump(1.0)
        c.check("S5 신뢰도 즉시 반영", abs(detect.worker.conf - 0.80) < 1e-9 and "0.80" in detect.conf_btn.text(),
                detect.conf_btn.text())
        detect.conf.setValue(50); c.pump(0.5)

        # ---- S6 탐지 중 맵 바꾸기 → 되돌리기 ----
        before = len(detect.victims.items)
        other = next((j for j in range(detect.map_box.count()) if detect.map_box.itemText(j) != RUN), None)
        if other is None:
            c.check("S6 다른 맵 (건너뜀 — 회차가 하나뿐)", True)
        else:
            detect.map_box.setCurrentIndex(other); c.pump(3)
            c.check("S6 맵을 바꾸면 객체 초기화", len(detect.victims.items) < before and detect.worker is not None,
                    f"{before} → {len(detect.victims.items)} ({detect.map_box.currentText()})")
            detect.map_box.setCurrentText(RUN); c.pump(8)
            c.check("S6 원래 맵으로 돌아와 다시 쌓임", len(detect.victims.items) > 0, plain(detect.summary))

        # ---- S7 다른 페이지를 다녀와도 탐지 유지 ----
        nav[1].click(); c.pump(2); nav[0].click(); c.pump(2); nav[2].click(); c.pump(1)
        c.check("S7 페이지 왕복 후에도 탐지 중", detect.worker is not None and win.hub.active)

        # ---- S8 정지 → 드론 · 시야선 지움 (핀은 남김), 재시작 → 초기화 ----
        detect.detect_btn.click()
        c.check("S8 정지", c.wait(lambda: detect.worker is None and detect.detect_btn.text() == "탐지 시작", 10))
        c.pump(0.5)
        c.check("S8 정지하면 드론 · 시야선 지움, 핀은 남김",
                detect._camera is None and detect.viewport._drone_part is None
                and detect.viewport._camera_part is None and len(detect._shown) > 0)
        c.shot(win, "s8_stopped")
        n_before = len(detect.victims.items)
        detect.detect_btn.click()
        c.check("S8 재시작하면 목록 초기화", c.wait(lambda: detect.detect_btn.text() == "탐지 정지", 5)
                and len(detect.victims.items) <= n_before, f"{n_before} → {len(detect.victims.items)}")
        c.pump(6)

        # ---- S9 파이가 죽음 (탐지 중) ----
        stop_pi(pi)
        c.check("S9 링크 끊김 감지", c.wait(lambda: not win.hub.active, 15), status.note.text())
        c.check("S9 탐지 자동 정지", c.wait(lambda: detect.worker is None, 15))
        c.pump(0.5)
        c.check("S9 드론도 지움", detect._camera is None and detect.viewport._drone_part is None)
        c.pump(1)
        c.check("S9 PiP · 버튼 안내", NO_LINK in detect.pip.label.text() and not detect.detect_btn.isEnabled(),
                detect.detect_note.text())
        c.check("S9 상태 페이지에 사유", "끊" in status.note.text() or "없" in status.note.text(), status.note.text())
        c.shot(win, "s9_dropped")

        # ---- S10 파이 다시 켜고 재연결 → 재탐지 ----
        pi = start_pi()
        nav[0].click(); c.pump(0.3)
        status.connect_btn.click()
        c.check("S10 재연결", c.wait(lambda: win.hub.active and status.capture_btn.isEnabled(), 10),
                status.note.text())
        nav[2].click(); c.pump(0.5)
        detect.detect_btn.click()
        c.check("S10 재탐지 핀", c.wait(lambda: len(detect._shown) > 0, 40), plain(detect.summary))

        # ---- S11 탐지 중에 창 닫기 ----
        t0 = time.monotonic()
        win.close(); c.pump(1)
        c.check("S11 닫기 — 스레드 정리", detect.worker is None and not win.hub.active,
                f"{time.monotonic() - t0:.1f}s")
    finally:
        if pi is not None:
            stop_pi(pi)
        if win.isVisible():
            win.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="실제 메인 창으로 탐지 시나리오를 점검한다")
    ap.add_argument("--shots", type=Path, help="단계별 화면 캡처를 저장할 폴더 (macOS)")
    args = ap.parse_args(argv)
    if not Path(os.environ["AEROVER_CAMERA"]).is_file():
        print(f"폰 K 가 없다 — 먼저: uv run python -m tools.fake_pi {SESSIONS_DIR / RUN} "
              f"--odm-camera 3D_model/{RUN}/cameras.json")
        return 1
    if args.shots is not None:
        args.shots.mkdir(parents=True, exist_ok=True)

    configure_surface_format()          # QApplication 전이어야 한다
    app = QApplication(sys.argv[:1])
    from src.ui.palette import app_qss
    app.setStyleSheet(app_qss())
    QSettings("aerover", "aerover").setValue("detect/map", RUN)

    c = Checker(app, args.shots)
    scenario(c, app)

    print("\n==== 예외 ====")
    print("\n".join(c.errors) if c.errors else "없음")
    fails = [r for r in c.results if not r[1]]
    print(f"\n==== {len(c.results) - len(fails)}/{len(c.results)} 통과 ====")
    for name, _, info in fails:
        print("FAIL", name, info)
    return 1 if fails or c.errors else 0


if __name__ == "__main__":
    sys.exit(main())
