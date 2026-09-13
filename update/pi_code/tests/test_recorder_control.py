"""recorder · control · hardware · camera 설정 · 프로그램 조립."""
import json
import sys
import threading
import time
from pathlib import Path

import pytest
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder

from src.camera import settings as cam_settings
from src.camera.factory import open_record_camera, open_stream_camera, open_web_camera
from src.control import cli
from src.control.protocol import dispatch, handlers_for
from src.control.unix_server import UnixCommandServer
from src.hardware.button import ButtonControl, ButtonSettings
from src.hardware.led import StatusLed
from src.recorder.raw_output import SafeRawOutput
from src.recorder.recorder import Recorder, RecordSettings


# ---- recorder ----

def _recorder(tmp_path: Path, gap_ms: int = 100) -> tuple[Recorder, Picamera2]:
    cam = Picamera2()
    rec = Recorder(cam, RecordSettings(rec_dir=str(tmp_path / "rec"), fps=30, gap_ms=gap_ms),
                   make_encoder=lambda: H264Encoder(bitrate=1, repeat=True, iperiod=30),
                   make_output=lambda vid, pts: SafeRawOutput(vid, pts, sync_every=2, gap_ms=gap_ms))
    return rec, cam


def test_recorder_start_stop_writes_h264_pts_and_log(tmp_path):
    rec, cam = _recorder(tmp_path)
    assert rec.status() == {"ok": True, "recording": False}

    r = rec.start()
    assert r["ok"] and rec.recording and cam.encoders
    assert rec.start()["ok"] is False                     # 중복 start
    time.sleep(0.05)
    assert rec.status()["recording"] is True

    r = rec.stop()
    assert r["ok"] and not rec.recording and not cam.encoders
    assert rec.stop()["ok"] is False                      # 중복 stop
    base = Path(r["file"]).with_suffix("")
    assert base.with_suffix(".h264").stat().st_size > 0
    pts = base.with_suffix(".pts").read_text().splitlines()
    assert pts[0] == "# timecode format v2" and len(pts) - 1 == r["frames"]
    log = base.with_suffix(".log").read_text(encoding="utf-8")
    assert "실제 프레임" in log and str(r["frames"]) in log


def test_recorder_toggle_and_gap_detection(tmp_path):
    rec, _ = _recorder(tmp_path, gap_ms=10)
    assert rec.toggle()["msg"] == "녹화 시작"
    rec.output.outputframe(b"f", timestamp=0)
    rec.output.outputframe(b"f", timestamp=50_000)       # 50ms > 10ms → 결손
    assert rec.gap_count() >= 1
    assert rec.toggle()["msg"] == "녹화 종료"


# ---- control ----

def test_dispatch_routes_commands_to_json_lines():
    class Target:
        def start(self): return {"ok": True, "msg": "녹화 시작"}
        def stop(self): return {"ok": False}
        def status(self): return {"ok": True, "recording": False}
        def toggle(self): return {"ok": True}

    handlers = handlers_for(Target())
    assert set(handlers) == {"start", "stop", "status", "toggle"}
    assert json.loads(dispatch(b"  START\n", handlers)) == {"ok": True, "msg": "녹화 시작"}
    res = json.loads(dispatch(b"bogus", handlers))
    assert res["ok"] is False and "bogus" in res["msg"]
    assert dispatch(b"status", handlers).endswith(b"\n")


def test_cli_describe_and_exit_codes(capsys):
    assert cli.describe({"ok": True, "recording": False}) == ["대기 중 (녹화 안 함)"]
    lines = cli.describe({"ok": True, "msg": "녹화 종료", "file": "f.h264",
                          "seconds": 3.0, "frames": 90, "expected": 90, "gaps": 0})
    assert lines[0] == "녹화 종료" and any("f.h264" in l for l in lines)

    assert cli.main(["--help"]) == 0
    assert "toggle" in capsys.readouterr().out
    assert cli.main(["status"], path=str(Path("no") / "such" / "sock")) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="유닉스 소켓은 파이·맥에서만")
def test_unix_server_round_trip(tmp_path):
    path = tmp_path / "run" / "control.sock"
    ticks = []
    server = UnixCommandServer(str(path), {"status": lambda: {"ok": True, "recording": False}},
                               on_tick=lambda: ticks.append(1), poll_sec=0.05)
    server.open()
    running = threading.Event()
    running.set()
    t = threading.Thread(target=server.serve, args=(running,), daemon=True)
    t.start()
    try:
        assert cli.send_command("status", str(path)) == {"ok": True, "recording": False}
        assert cli.main(["status"], path=str(path)) == 0
    finally:
        running.clear()
        t.join(2.0)
        server.close()
    assert not path.exists() and ticks


# ---- hardware ----

class FakeLed:
    def __init__(self) -> None:
        self.calls: list = []

    def blink(self, **kw): self.calls.append(("blink", kw["on_time"]))
    def on(self): self.calls.append(("on",))
    def off(self): self.calls.append(("off",))
    def close(self): self.calls.append(("close",))


def test_status_led_modes_and_flash():
    led = FakeLed()
    s = StatusLed(led)
    s.update(False)
    s.update(False)                                   # 같은 모드는 다시 안 보낸다
    s.update(True)
    s.flash(10.0)
    s.update(True)                                    # 경고가 우선
    assert led.calls == [("blink", 0.05), ("on",), ("blink", 0.08)]
    s.close()
    assert led.calls[-2:] == [("off",), ("close",)]
    assert StatusLed(None).update(True) is None        # GPIO 없음 — 아무것도 안 한다


class FakeButton:
    when_pressed = when_held = when_released = None

    def close(self): pass


class FakeTarget:
    recording = False

    def toggle(self):
        self.recording = not self.recording
        return {"ok": True, "msg": "toggled"}


def test_button_short_press_toggles_and_hold_exits_only_when_enabled():
    btn, target, exits = FakeButton(), FakeTarget(), []
    ButtonControl(btn, target, StatusLed(None), exits.append, ButtonSettings(enable_hold_exit=False))
    btn.when_pressed(); btn.when_released()
    assert target.recording is True
    btn.when_pressed(); btn.when_held(); btn.when_released()
    assert exits == [] and target.recording is False   # 길게 눌러도 종료 안 함, 토글은 됨

    ButtonControl(btn, target, StatusLed(None), lambda: exits.append(1),
                  ButtonSettings(enable_hold_exit=True))
    btn.when_pressed(); btn.when_held(); btn.when_released()
    assert exits == [1] and target.recording is False  # 종료용 누름은 토글하지 않는다


# ---- camera settings · factory ----

def test_exposure_controls_from_env():
    assert cam_settings.exposure_controls_from_env({}) == {}
    assert cam_settings.exposure_controls_from_env({"EXP": "2500", "GAIN": "4"}) == {
        "AeEnable": False, "ExposureTime": 2500, "AnalogueGain": 4.0}


def test_factories_build_expected_configs():
    cam = open_stream_camera(cam_settings.StreamCameraSettings(), {"AeEnable": False})
    assert cam.config["main"] == {"size": (1280, 960)} and cam.config["raw"] == {"size": (2028, 1520)}
    assert cam.config["transform"].hflip == 1 and cam.config["controls"] == {"AeEnable": False}

    cam = open_record_camera(cam_settings.RecordCameraSettings())
    assert cam.config["controls"] == {"FrameRate": 30} and "transform" not in cam.config

    cam = open_web_camera(cam_settings.WebCameraSettings(), {})
    assert cam.config["lores"] == {"size": (640, 360), "format": "YUV420"}


# ---- 프로그램 조립 ----

def test_every_program_imports_with_fake_camera():
    import importlib
    for name in ("stream", "record", "udp", "web"):
        mod = importlib.import_module(f"src.programs.{name}")
        assert callable(mod.main)


def test_config_paths_are_repo_relative():
    from src import config
    for p in (config.REC_DIR, config.RUN_DIR):
        assert config.ROOT in p.parents
    assert Path(config.SOCK_PATH).parent == config.RUN_DIR
    assert (config.ROOT / "app.py").is_file()
