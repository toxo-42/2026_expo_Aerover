"""드론 상태 페이지 (main) — 콘티 2절.

    ┌────────────────────────┬──────────────────┐
    │  실시간 영상            │  텔레메트리 계기판 │
    ├────────────────────────┤                  │
    │  수집 제어              │                  │
    └────────────────────────┴──────────────────┘

계기판은 `core/telemetry` 패키지의 공개 API 만 쓴다.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.config import PI_HOST, PI_PORT, SERIAL_PORT, SESSIONS_DIR, TARGET_AGL_M
from src.core import telemetry as tm
from src.core.capture import CaptureSession
from src.core.link import LinkHub
from src.ui.metric import MetricRow
from src.ui.palette import STATUS_OK_TEXT, STATUS_RED, TEXT_DIM, TEXT_PRIMARY, WARN_AMBER
from src.ui.video import VideoView

HUD_MARGIN = 16
TELEMETRY_WIDTH = 260
REFRESH_MS = 200
DEFAULT_INTERVAL = 3.0
DEFAULT_TARGET = 120

# 판정 기준. **다른 모듈에서 가져오지 않고 여기 둔다** — 이미지 필터 상수를
# 화면 판정에 같이 쓰면 필터를 고칠 때 계기판이 같이 움직인다.
MIN_SATS = 6  # 이하면 fix 를 신뢰하지 않는다
BATT_WARN_PCT = 40
BATT_CRIT_PCT = 20
ALT_TOLERANCE = 0.10  # 목표 고도 ±10% 안이면 정상


class TelemetryPanel(QFrame):
    """전체 텔레메트리. 평소엔 숨어 있고 [+] 로 펼친다 (시안 1절).

    갱신 타이머는 **여기 하나뿐이다.** 상단 HUD 는 같은 스냅샷을 받아 쓴다 —
    타이머를 둘 두면 두 화면이 서로 다른 시점의 값을 보여준다.
    """

    refreshed = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(0)

        head = QLabel("텔레메트리")
        head.setStyleSheet("font-weight:600; padding-bottom:6px;")
        lay.addWidget(head)

        self.rows = {
            k: MetricRow(k)
            for k in ("LINK", "BATT", "MODE", "GPS", "ALT", "SPD", "HOME", "ATT")
        }
        for row in self.rows.values():
            lay.addWidget(row)

        self.note = QLabel("")
        self.note.setObjectName("dim")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color:{TEXT_DIM}; padding-top:8px;")
        lay.addWidget(self.note)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(REFRESH_MS)
        self.refresh()

    def refresh(self) -> None:
        snap = tm.get_telemetry()
        self._link(snap)
        self._battery(snap)
        self._mode(snap)
        self._gps(snap)
        self._altitude(snap)
        self._attitude(snap)

        self.refreshed.emit(snap)

        err = tm.last_error(snap)
        self.note.setText(f"오류 — {err}" if err else f"소스: USB {SERIAL_PORT}")
        self.note.setStyleSheet(
            f"color:{STATUS_RED if err else TEXT_DIM}; padding-top:8px;"
        )

    # ---- 항목별 ----

    def _link(self, snap) -> None:
        status = tm.link_status(snap)
        color = {"OK": STATUS_OK_TEXT, "WEAK": WARN_AMBER}.get(status, STATUS_RED)
        lq = snap["link"]["up_lq"]
        self.rows["LINK"].set(status, f"LQ {lq}" if status != "NO_DATA" else "", color)

    def _battery(self, snap) -> None:
        if tm.age(snap, "battery") is None:
            self.rows["BATT"].clear()
            return
        b = snap["battery"]
        pct = b["remaining_pct"]
        color = (
            STATUS_RED
            if pct < BATT_CRIT_PCT
            else WARN_AMBER
            if pct < BATT_WARN_PCT
            else STATUS_OK_TEXT
        )
        self.rows["BATT"].set(
            f"{b['voltage']:.1f}V", f"{pct}% · {b['current']:.1f}A", color
        )

    def _gps(self, snap) -> None:
        g = snap["gps"]
        fixed = g["sats"] >= MIN_SATS

        if tm.age(snap, "gps") is None:
            # ALT 는 여기 없다. 기압계가 따로 주므로 _altitude 가 담당한다.
            for k in ("GPS", "SPD", "HOME"):
                self.rows[k].clear("미수신")
            return

        sats = g["sats"]
        self.rows["GPS"].set(
            f"{sats} sats",
            f"{g['lat']:.5f}, {g['lon']:.5f}" if sats else "fix 없음",
            STATUS_OK_TEXT if fixed else (WARN_AMBER if sats else STATUS_RED),
        )

        if not sats:
            # fix 가 없으면 속도·거리가 무의미하다.
            for k in ("SPD", "HOME"):
                self.rows[k].clear("fix 없음")
            return

        self.rows["SPD"].set(
            f"{g['speed_kmh']:.1f} km/h", f"HDG {round(g['heading']) % 360}°"
        )

        dist = tm.distance_from_home(snap)
        bearing = tm.bearing_from_home(snap)
        if dist is None:
            # fix 는 있는데 거리가 없다 = HOME 을 아직 안 잡았다. 상단 [HOME 설정] 으로 잡는다.
            self.rows["HOME"].clear("미설정")
        else:
            self.rows["HOME"].set(f"{dist:.1f}m", f"방위 {bearing:.0f}°")

    def _altitude(self, snap) -> None:
        """고도는 **기압계**에서 온다. GPS fix 와 무관하다 — GPS 고도(`gps.alt_m`)보다
        기압계가 정확해서 일부러 이쪽을 쓴다. GPS 를 단 뒤(2026-09-08)에도 그대로 둔다.
        기압 고도는 해발이 아니라 FC 가 잡은 기준점 기준이다."""
        if tm.age(snap, "baro") is None:
            self.rows["ALT"].clear("미수신")
            return
        alt = snap["baro"]["alt_m"]
        vs = snap["vario"]["vspeed_ms"] if tm.age(snap, "vario") is not None else None
        on_target = abs(alt - TARGET_AGL_M) <= TARGET_AGL_M * ALT_TOLERANCE
        sub = f"기압 · 목표 {TARGET_AGL_M}m"
        if vs is not None:
            sub += f" · {vs:+.1f}m/s"
        self.rows["ALT"].set(f"{alt:.1f}m", sub, STATUS_OK_TEXT if on_target else None)

    def _mode(self, snap) -> None:
        if tm.age(snap, "mode") is None:
            self.rows["MODE"].clear("미수신")
            return
        text = snap["mode"]["text"]
        # 앞의 '!' 는 arming 불가 상태를 뜻한다 (예: !ERR).
        self.rows["MODE"].set(
            text or "—", "", STATUS_RED if text.startswith("!") else STATUS_OK_TEXT
        )

    def _attitude(self, snap) -> None:
        att = snap.get("attitude")
        if tm.age(snap, "attitude") is None or not att:
            self.rows["ATT"].clear("미수신")
            return
        self.rows["ATT"].set(
            f"R{att['roll']:+.0f}° P{att['pitch']:+.0f}°", f"YAW {att['yaw']:.0f}°"
        )


class HudChip(QFrame):
    """상단 HUD 한 칸 — 이름 + 값. 값 색으로 상태를 말한다."""

    def __init__(self, key: str, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.key = QLabel(key)
        self.key.setObjectName("metricKey")
        self.value = QLabel("—")
        self.value.setObjectName("chipValue")
        lay.addWidget(self.key)
        lay.addWidget(self.value)

    def set(self, text: str, color: str = TEXT_PRIMARY) -> None:
        self.value.setText(text)
        self.value.setStyleSheet(f"color:{color};")


class StatusPage(QWidget):
    """실시간 영상 + 수집 제어 + 텔레메트리 계기판.

    링크는 **셸의 `LinkHub` 가 소유한다.** 이 페이지는 연결을 걸고 끊는
    유일한 곳이지만 워커를 직접 만들지는 않는다 — 파이가 한 번에 한
    클라이언트만 받아서, 탐지 페이지와 소켓을 다투면 안 된다.
    """

    def __init__(self, hub: LinkHub, parent=None) -> None:
        super().__init__(parent)
        self.hub = hub
        self.capture: CaptureSession | None = None     # 끝난 뒤에도 남겨 결과 문구에 쓴다
        self._saving = False
        self._frame_times: list[float] = []

        # 영상이 화면을 채우고, 제어부는 아래 고정 패널에 둔다 (시안 1절).
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        self.video = VideoView()
        root.addWidget(self.video, 1)
        root.addWidget(self._build_control())

        # 영상 위에 뜨는 것들 — 레이아웃이 아니라 좌표로 얹는다.
        self.hud = self._build_hud()
        self.telemetry = TelemetryPanel(self)
        self.telemetry.setObjectName("hud")
        self.telemetry.setFixedWidth(TELEMETRY_WIDTH)
        self.telemetry.refreshed.connect(self._on_telemetry)
        self.telemetry.refresh()  # 첫 틱(200ms)을 기다리지 않고 칩을 채운다
        self.telemetry.hide()
        for w in (self.hud, self.telemetry):
            w.raise_()

        self.hub.frame_ready.connect(self._on_frame)
        self.hub.connected.connect(
            lambda: self._set_note(self.link_note, "연결됨", STATUS_OK_TEXT)
        )
        self.hub.failed.connect(self._on_failed)
        self.hub.closed.connect(self._on_link_finished)

    # ---- 구성 ----

    def _build_hud(self) -> QFrame:
        """상단 요약. GPS 칩도 여기 있다 — 모듈을 단 뒤(2026-09-08) 실제로 값이 찬다.
        [HOME 설정] 은 **이 자리를 이륙지점으로 잡는다.** 자동으로 안 잡는 이유는
        `core/telemetry/home.py` 주석 참고."""
        box = QFrame(self)
        box.setObjectName("hud")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(14, 8, 10, 8)
        lay.setSpacing(20)

        self.chips = {k: HudChip(k) for k in ("LINK", "BATT", "ALT", "MODE", "GPS")}
        for chip in self.chips.values():
            lay.addWidget(chip)
        lay.addStretch(1)

        self.home_btn = QPushButton("HOME 설정")
        self.home_btn.setObjectName("hudButton")
        self.home_btn.setEnabled(False)  # fix 를 받기 전엔 못 누른다
        self.home_btn.clicked.connect(self._set_home)
        lay.addWidget(self.home_btn)

        self.expand_btn = QPushButton("＋ 상세")
        self.expand_btn.setObjectName("hudButton")
        self.expand_btn.setCheckable(True)
        self.expand_btn.toggled.connect(self._toggle_telemetry)
        lay.addWidget(self.expand_btn)
        return box

    def _set_home(self) -> None:
        """지금 좌표를 이륙지점으로 잡는다. 이미 잡혀 있어도 덮어쓴다."""
        if tm.set_home(tm.get_telemetry()):
            self.home_btn.setText("HOME 재설정")

    def _toggle_telemetry(self, on: bool) -> None:
        self.telemetry.setVisible(on)
        self.expand_btn.setText("－ 접기" if on else "＋ 상세")
        self.resizeEvent(None)

    def _on_telemetry(self, snap) -> None:
        """펼친 패널과 **같은 스냅샷**으로 상단 칩을 채운다."""
        status = tm.link_status(snap)
        self.chips["LINK"].set(
            status, {"OK": STATUS_OK_TEXT, "WEAK": WARN_AMBER}.get(status, STATUS_RED)
        )

        if tm.age(snap, "battery") is None:
            self.chips["BATT"].set("—", TEXT_DIM)
        else:
            b = snap["battery"]
            pct = b["remaining_pct"]
            self.chips["BATT"].set(
                f"{b['voltage']:.1f}V · {pct}%",
                STATUS_RED
                if pct < BATT_CRIT_PCT
                else WARN_AMBER
                if pct < BATT_WARN_PCT
                else STATUS_OK_TEXT,
            )

        if tm.age(snap, "baro") is None:
            self.chips["ALT"].set("—", TEXT_DIM)
        else:
            alt = snap["baro"]["alt_m"]
            on_target = abs(alt - TARGET_AGL_M) <= TARGET_AGL_M * ALT_TOLERANCE
            self.chips["ALT"].set(
                f"{alt:.1f}m", STATUS_OK_TEXT if on_target else TEXT_PRIMARY
            )

        if tm.age(snap, "mode") is None:
            self.chips["MODE"].set("—", TEXT_DIM)
        else:
            text = snap["mode"]["text"]
            self.chips["MODE"].set(
                text or "—", STATUS_RED if text.startswith("!") else STATUS_OK_TEXT
            )

        if tm.age(snap, "gps") is None:
            self.chips["GPS"].set("—", TEXT_DIM)
            self.home_btn.setEnabled(False)
            return

        sats = snap["gps"]["sats"]
        fixed = sats >= MIN_SATS
        self.chips["GPS"].set(
            f"{sats} sats",
            STATUS_OK_TEXT if fixed else (WARN_AMBER if sats else STATUS_RED),
        )
        # fix 가 못 미더우면 HOME 을 못 잡게 막는다 — 틀린 자리를 잡으면 조용히 계속 틀린다.
        self.home_btn.setEnabled(fixed)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        view = self.video.geometry()
        m = HUD_MARGIN
        self.hud.setGeometry(
            view.x() + m,
            view.y() + m,
            view.width() - 2 * m,
            self.hud.sizeHint().height(),
        )
        if not self.telemetry.isHidden():
            top = self.hud.geometry().bottom() + m
            self.telemetry.setGeometry(
                view.right() - m - TELEMETRY_WIDTH + 1,
                top,
                TELEMETRY_WIDTH,
                min(self.telemetry.sizeHint().height(), view.bottom() - m - top),
            )

    def _build_control(self) -> QFrame:
        """하단 고정 패널 — 링크와 수집을 한 줄에 둔다."""
        box = QFrame()
        box.setObjectName("panel")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        # 파이 주소를 화면에서 바꾼다. 기존 코드는 세 파일에 하드코딩돼 있었다.
        self.addr = QLineEdit(f"{PI_HOST}:{PI_PORT}")
        self.addr.setFixedWidth(170)
        self.connect_btn = QPushButton("연결")
        self.connect_btn.clicked.connect(self._toggle_link)
        self.link_note = QLabel("")
        self.link_note.setObjectName("dim")

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.2, 30.0)
        self.interval.setSingleStep(0.5)
        self.interval.setValue(DEFAULT_INTERVAL)
        self.interval.setSuffix(" 초")

        self.target = QSpinBox()
        self.target.setRange(0, 2000)
        self.target.setValue(DEFAULT_TARGET)
        self.target.setSuffix(" 장")
        self.target.setToolTip("0 이면 정지할 때까지 계속 저장한다")

        self.capture_btn = QPushButton("수집 시작")
        self.capture_btn.setEnabled(False)
        self.capture_btn.clicked.connect(self._toggle_capture)

        row.addWidget(QLabel("파이"))
        row.addWidget(self.addr)
        row.addWidget(self.connect_btn)
        row.addWidget(self.link_note, 1)
        row.addWidget(self._sep())
        row.addWidget(QLabel("간격"))
        row.addWidget(self.interval)
        row.addWidget(QLabel("목표"))
        row.addWidget(self.target)
        row.addWidget(self.capture_btn)
        lay.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        lay.addWidget(self.progress)

        self.capture_note = QLabel("연결하면 수집할 수 있다")
        self.capture_note.setObjectName("dim")
        lay.addWidget(self.capture_note)
        return box

    @staticmethod
    def _sep() -> QFrame:
        line = QFrame()
        line.setObjectName("hudSep")
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedWidth(1)
        return line

    # ---- 링크 ----

    def _toggle_link(self) -> None:
        if self.hub.active:
            self._stop_link()
            return

        host, _, port = self.addr.text().strip().partition(":")
        try:
            port = int(port or PI_PORT)
        except ValueError:
            self._set_note(self.link_note, "주소 형식은 호스트:포트", STATUS_RED)
            return

        self.hub.connect_to(host, port)

        self.addr.setEnabled(False)
        self.connect_btn.setText("해제")
        self._set_note(self.link_note, "연결 중…", TEXT_DIM)

    def _stop_link(self) -> None:
        if not self.hub.active:
            return
        self._stop_capture()
        self.hub.disconnect_from()

    def _on_link_finished(self) -> None:
        self._frame_times.clear()
        self._stop_capture()
        self.addr.setEnabled(True)
        self.connect_btn.setText("연결")
        self.capture_btn.setEnabled(False)
        self.video.clear()

    def _on_failed(self, message: str) -> None:
        self._set_note(self.link_note, message, STATUS_RED)

    def _on_frame(self, image: QImage, raw: bytes) -> None:
        self.video.set_frame(image)
        self.capture_btn.setEnabled(True)

        now = time.time()
        self._frame_times.append(now)
        del self._frame_times[:-15]
        span = self._frame_times[-1] - self._frame_times[0]
        fps = (len(self._frame_times) - 1) / span if span > 0 else 0.0
        self._set_note(
            self.link_note,
            f"{image.width()}x{image.height()} · {fps:.1f} fps",
            STATUS_OK_TEXT,
        )

        if self._saving and self.capture.offer(raw, now):
            self._on_saved()

    # ---- 수집 ----

    def _toggle_capture(self) -> None:
        if self._saving:
            self._stop_capture("수집 중지")
        else:
            self._start_capture()

    def _start_capture(self) -> None:
        self.capture = CaptureSession(SESSIONS_DIR, self.interval.value(), self.target.value())
        self._saving = True
        self.capture_btn.setText("정지")
        self.interval.setEnabled(False)
        self.target.setEnabled(False)
        self.progress.setVisible(bool(self.target.value()))
        self.progress.setMaximum(self.target.value() or 1)
        self.progress.setValue(0)
        self._update_capture_note()

    def _stop_capture(self, reason: str = "") -> None:
        if not self._saving:
            return
        self._saving = False
        self.capture_btn.setText("수집 시작")
        self.interval.setEnabled(True)
        self.target.setEnabled(True)
        self._update_capture_note(reason)

    def _on_saved(self) -> None:
        self.progress.setValue(self.capture.saved)
        if self.capture.done:
            self._stop_capture(f"수집 완료 — {self.capture.saved}장")
        else:
            self._update_capture_note()

    def _update_capture_note(self, reason: str = "") -> None:
        if self.capture is None:
            return
        c = self.capture
        count = f"{c.saved}/{c.target}" if c.target else f"{c.saved}장"
        self.capture_note.setText(
            f"{reason + ' · ' if reason else ''}{count}  →  {c.dir}/"
        )

    @staticmethod
    def _set_note(label: QLabel, text: str, color: str) -> None:
        label.setText(text)
        label.setStyleSheet(f"color:{color};")
