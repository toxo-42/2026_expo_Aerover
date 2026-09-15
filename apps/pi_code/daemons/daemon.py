#!/usr/bin/env python3
"""
드론 카메라 녹화 데몬 (GPIO 버튼 + 상태 LED 지원)
  저장 : /home/drone/drone/rec/
  제어 : cam start / cam status / cam stop / cam toggle
  버튼 : 짧게 누름 → 녹화 토글, 길게 누름 → 데몬 안전 종료
  LED  : 대기(2초에 한 번 깜빡) / 녹화(상시 점등) / 경고(빠른 점멸)
"""

import os, time, json, socket, signal, threading, datetime

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import Output

# ── 설정 ────────────────────────────────────────────
BASE_DIR   = "/home/drone/drone"
REC_DIR    = os.path.join(BASE_DIR, "rec")
SOCK_PATH  = "/run/dronecam/control.sock"

WIDTH, HEIGHT = 1920, 1080
FPS        = 30
BITRATE    = 8_000_000
SYNC_EVERY = 15      # N프레임마다 fsync (30fps → 약 0.5초)
GAP_MS     = 100     # 결손으로 판정할 프레임 간격

# ── GPIO 설정 (BCM 번호) ────────────────────────────
BTN_PIN    = 17      # 버튼: GPIO17(11번 핀) ── 스위치 ── GND, 내부 풀업 사용
LED_PIN    = 27      # LED : GPIO27(13번 핀) ── 330Ω ── LED ── GND
BOUNCE_S   = 0.05    # 채터링 제거 시간
HOLD_SEC   = 3.0     # 이 시간 이상 누르면 종료로 판정
ENABLE_HOLD_EXIT = False   # 길게 눌러 데몬 종료 기능 (오조작이 걱정되면 False)
MIN_PRESS_SEC    = 0.0    # 이 시간보다 짧게 눌리면 무시 (예: 0.3 → 스치는 접촉 방지)

try:
    from gpiozero import Button, LED
    GPIO_OK = True
except Exception as _e:          # gpiozero 미설치 / 권한 없음
    Button = LED = None
    GPIO_OK = False
    _GPIO_ERR = _e


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


class SafeRawOutput(Output):
    """H.264 생스트림 + 프레임 타임스탬프를 버퍼링 없이 기록."""

    def __init__(self, video_path, pts_path, sync_every=SYNC_EVERY):
        super().__init__()
        self._vid = open(video_path, "wb", buffering=0)   # 파이썬 버퍼 우회
        self._pts = open(pts_path, "w")
        self._pts.write("# timecode format v2\n")
        self._sync_every = sync_every
        self._t0      = None
        self._last_ms = None
        self.frames   = 0
        self.gaps     = []          # [(프레임번호, 간격ms), ...]

    def outputframe(self, frame, keyframe=True, timestamp=None, *a, **kw):
        self._vid.write(frame)

        if timestamp is not None:
            if self._t0 is None:
                self._t0 = timestamp
            ms = (timestamp - self._t0) / 1000.0
            if self._last_ms is not None and (ms - self._last_ms) > GAP_MS:
                self.gaps.append((self.frames, round(ms - self._last_ms, 1)))
            self._last_ms = ms
            self._pts.write(f"{ms:.3f}\n")

        self.frames += 1
        if self.frames % self._sync_every == 0:
            self._flush()

    def _flush(self):
        self._pts.flush()
        os.fsync(self._vid.fileno())
        os.fsync(self._pts.fileno())

    def close(self):
        try:
            self._flush()
        finally:
            self._vid.close()
            self._pts.close()


class StatusLed:
    """상태 LED. gpiozero의 백그라운드 blink를 쓰므로 별도 스레드가 필요 없다."""

    def __init__(self, pin):
        self._led = LED(pin) if (GPIO_OK and pin is not None) else None
        self._mode = None
        self._flash_until = 0.0

    def _apply(self, mode):
        if self._led is None or mode == self._mode:
            return
        self._mode = mode
        if mode == "idle":
            self._led.blink(on_time=0.05, off_time=1.95)   # 대기: 살아있음 표시
        elif mode == "rec":
            self._led.on()                                 # 녹화 중: 상시 점등
        elif mode == "warn":
            self._led.blink(on_time=0.08, off_time=0.08)   # 경고: 빠른 점멸
        else:
            self._led.off()

    def flash(self, sec=1.5):
        """일정 시간 경고 점멸 (명령 실패, 프레임 결손 발생 등)."""
        self._flash_until = time.time() + sec

    def update(self, recording):
        if time.time() < self._flash_until:
            self._apply("warn")
        else:
            self._apply("rec" if recording else "idle")

    def close(self):
        if self._led is not None:
            self._led.off()
            self._led.close()


class Recorder:
    def __init__(self):
        self.lock    = threading.RLock()   # toggle()이 start/stop을 재진입 호출
        self.encoder = None
        self.output  = None
        self.base    = None
        self.t0      = None

        os.makedirs(REC_DIR, exist_ok=True)

        self.picam2 = Picamera2()
        self.picam2.configure(self.picam2.create_video_configuration(
            main={"size": (WIDTH, HEIGHT)},
            controls={"FrameRate": FPS},
            buffer_count=6,
        ))
        self.picam2.start()          # 카메라는 켜둔 채 대기 → 트리거 즉시 반응
        log(f"카메라 준비 완료 ({WIDTH}x{HEIGHT}@{FPS}), 저장 위치 {REC_DIR}")

    @property
    def recording(self):
        return self.encoder is not None

    def gap_count(self):
        out = self.output
        return len(out.gaps) if out else 0

    def start(self):
        with self.lock:
            if self.encoder:
                return {"ok": False, "msg": "이미 녹화 중",
                        "file": f"{self.base}.h264"}

            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.base = os.path.join(REC_DIR, f"flight_{stamp}")

            self.output  = SafeRawOutput(f"{self.base}.h264", f"{self.base}.pts")
            self.encoder = H264Encoder(bitrate=BITRATE, repeat=True, iperiod=FPS)
            self.picam2.start_encoder(self.encoder, self.output)
            self.t0 = time.time()

            log(f"녹화 시작 → {self.base}.h264")
            return {"ok": True, "msg": "녹화 시작", "file": f"{self.base}.h264"}

    def stop(self):
        with self.lock:
            if not self.encoder:
                return {"ok": False, "msg": "녹화 중이 아님"}

            self.picam2.stop_encoder(self.encoder)
            self.output.close()
            os.sync()

            dur = time.time() - self.t0
            res = {
                "ok": True, "msg": "녹화 종료",
                "file":     f"{self.base}.h264",
                "seconds":  round(dur, 1),
                "frames":   self.output.frames,
                "expected": int(dur * FPS),
                "gaps":     len(self.output.gaps),
            }

            with open(f"{self.base}.log", "w") as f:
                f.write(f"경과       : {res['seconds']} s\n")
                f.write(f"기대 프레임: {res['expected']}\n")
                f.write(f"실제 프레임: {res['frames']}\n")
                f.write(f"결손 구간  : {res['gaps']}건 (기준 {GAP_MS} ms)\n")
                for idx, d in self.output.gaps:
                    f.write(f"  frame {idx} 직후 {d} ms\n")

            self.encoder = self.output = None
            log(f"녹화 종료 {res}")
            return res

    def toggle(self):
        with self.lock:
            return self.stop() if self.encoder else self.start()

    def status(self):
        with self.lock:
            if not self.encoder:
                return {"ok": True, "recording": False}
            dur = time.time() - self.t0
            return {
                "ok": True, "recording": True,
                "file":     f"{self.base}.h264",
                "seconds":  round(dur, 1),
                "frames":   self.output.frames,
                "expected": int(dur * FPS),
                "gaps":     len(self.output.gaps),
            }

    def shutdown(self):
        self.stop()
        self.picam2.stop()
        self.picam2.close()


class GpioControl:
    """버튼 하나로 토글 + 길게 눌러 종료.

    when_released 시점에 판정하므로, 길게 누른 경우 토글이 함께 일어나지 않는다.
    콜백은 gpiozero 내부 스레드에서 실행되고 Recorder는 락으로 보호되므로
    소켓 명령과 동시에 들어와도 안전하다.
    """

    def __init__(self, rec, led, on_exit):
        self.rec     = rec
        self.led     = led
        self.on_exit = on_exit
        self._held   = False
        self._t_down = 0.0

        self.btn = Button(BTN_PIN, pull_up=True,
                          bounce_time=BOUNCE_S, hold_time=HOLD_SEC)
        self.btn.when_pressed  = self._on_pressed
        self.btn.when_held     = self._on_held
        self.btn.when_released = self._on_released
        log(f"버튼 GPIO{BTN_PIN}, LED GPIO{LED_PIN} 준비 완료")

    def _on_pressed(self):
        self._t_down = time.time()

    def _on_held(self):
        if not ENABLE_HOLD_EXIT:
            return
        self._held = True
        log(f"버튼 {HOLD_SEC}s 이상 눌림 → 데몬 종료 요청")
        self.led.flash(2.0)
        self.on_exit()

    def _on_released(self):
        if self._held:                 # 종료용 길게 누름이었음
            self._held = False
            return
        if time.time() - self._t_down < MIN_PRESS_SEC:
            return                     # 스치듯 눌린 입력 무시

        res = self.rec.toggle()
        log(f"버튼 입력 → {res.get('msg')}")
        if not res.get("ok"):
            self.led.flash(1.5)
        self.led.update(self.rec.recording)

    def close(self):
        self.btn.close()


def main():
    rec     = Recorder()
    led     = StatusLed(LED_PIN)
    running = threading.Event()
    running.set()

    led.update(False)

    gpio = None
    if GPIO_OK:
        try:
            gpio = GpioControl(rec, led, running.clear)
        except Exception as e:
            log(f"GPIO 초기화 실패, 소켓 제어만 사용: {e}")
    else:
        log(f"gpiozero 사용 불가, 소켓 제어만 사용: {_GPIO_ERR}")

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda s, f: running.clear())

    os.makedirs(os.path.dirname(SOCK_PATH), exist_ok=True)
    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    os.chmod(SOCK_PATH, 0o660)
    srv.listen(4)
    srv.settimeout(0.5)
    log(f"소켓 대기 중 {SOCK_PATH}")

    handlers = {"start":  rec.start,
                "stop":   rec.stop,
                "status": rec.status,
                "toggle": rec.toggle}

    last_gaps = 0

    try:
        while running.is_set():
            # 상태 LED 갱신 (accept 타임아웃 덕분에 최소 0.5초마다 실행)
            g = rec.gap_count()
            if g > last_gaps:
                led.flash(1.0)          # 프레임 결손 발생 시 경고 점멸
            last_gaps = g
            led.update(rec.recording)

            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                cmd = conn.recv(256).decode().strip().lower()
                fn  = handlers.get(cmd)
                res = fn() if fn else {"ok": False, "msg": f"알 수 없는 명령: {cmd}"}
                conn.sendall((json.dumps(res, ensure_ascii=False) + "\n").encode())
                led.update(rec.recording)
    finally:
        rec.shutdown()
        srv.close()
        if os.path.exists(SOCK_PATH):
            os.unlink(SOCK_PATH)
        if gpio:
            gpio.close()
        led.close()
        log("데몬 종료")


if __name__ == "__main__":
    main()
