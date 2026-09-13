"""드론 카메라 녹화 데몬 (GPIO 버튼 + 상태 LED)  (원본 daemons/daemon.py 대체)

  저장 : config.REC_DIR (app.py 옆 rec/)
  제어 : python app.py cam start / status / stop / toggle  — 소켓은 config.SOCK_PATH
  버튼 : 짧게 누름 → 녹화 토글, 길게 누름 → 데몬 안전 종료 (ButtonSettings.enable_hold_exit, 기본 꺼짐)
  LED  : 대기(2초에 한 번 깜빡) / 녹화(상시 점등) / 경고(빠른 점멸)
"""
import signal
import threading

from picamera2.encoders import H264Encoder

from src.camera.factory import open_record_camera
from src.camera.settings import RecordCameraSettings
from src.config import (BITRATE, BOUNCE_S, BTN_PIN, GAP_MS, LED_PIN, REC_DIR, SOCK_PATH,
                        SYNC_EVERY)
from src.control.protocol import handlers_for
from src.control.unix_server import UnixCommandServer
from src.hardware.button import ButtonControl, ButtonSettings
from src.hardware.gpio import GPIO_ERROR, gpio_available, make_button, make_led
from src.hardware.led import StatusLed
from src.log import log
from src.recorder.raw_output import SafeRawOutput
from src.recorder.recorder import Recorder, RecordSettings


def main() -> None:
    cam_settings = RecordCameraSettings()
    picam2 = open_record_camera(cam_settings)
    picam2.start()          # 카메라는 켜둔 채 대기 → 트리거 즉시 반응
    w, h = cam_settings.size
    log(f"카메라 준비 완료 ({w}x{h}@{cam_settings.fps}), 저장 위치 {REC_DIR}")

    rec = Recorder(
        picam2,
        RecordSettings(rec_dir=str(REC_DIR), fps=cam_settings.fps, gap_ms=GAP_MS),
        make_encoder=lambda: H264Encoder(bitrate=BITRATE, repeat=True, iperiod=cam_settings.fps),
        make_output=lambda vid, pts: SafeRawOutput(vid, pts, SYNC_EVERY, GAP_MS),
    )
    led = StatusLed(make_led(LED_PIN))
    running = threading.Event()
    running.set()

    led.update(False)

    button_settings = ButtonSettings()
    gpio = None
    if gpio_available():
        try:
            btn = make_button(BTN_PIN, BOUNCE_S, button_settings.hold_sec)
            gpio = ButtonControl(btn, rec, led, running.clear, button_settings)
            log(f"버튼 GPIO{BTN_PIN}, LED GPIO{LED_PIN} 준비 완료")
        except Exception as e:
            log(f"GPIO 초기화 실패, 소켓 제어만 사용: {e}")
    else:
        log(f"gpiozero 사용 불가, 소켓 제어만 사용: {GPIO_ERROR}")

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda s, f: running.clear())

    last_gaps = 0

    def on_tick() -> None:
        nonlocal last_gaps
        g = rec.gap_count()
        if g > last_gaps:
            led.flash(1.0)          # 프레임 결손 발생 시 경고 점멸
        last_gaps = g
        led.update(rec.recording)

    server = UnixCommandServer(SOCK_PATH, handlers_for(rec), on_tick)
    server.open()

    try:
        server.serve(running)
    finally:
        rec.stop()
        picam2.stop()
        picam2.close()
        server.close()
        if gpio:
            gpio.close()
        led.close()
        log("데몬 종료")
