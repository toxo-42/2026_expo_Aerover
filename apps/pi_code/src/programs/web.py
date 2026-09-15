"""브라우저로 드론 카메라 미리보기 + 녹화 시작/종료 (새로 작성)

    http://<파이IP>:8000/

카메라 하나에 인코더 두 개를 붙인다.
  main  1920x1080 → H.264 녹화 (Recorder, 버튼을 누를 때만)
  lores  640x360  → MJPEG 미리보기 (항상)
Pi 4 는 둘 다 하드웨어 인코더라 동시에 돈다.

**카메라는 한 프로세스만 열 수 있다.** aerover-cam(지상국 스트림)·dronecam(녹화 데몬)과 동시에 못 쓴다.
"""
from picamera2.encoders import H264Encoder, MJPEGEncoder
from picamera2.outputs import FileOutput

from src.camera.factory import open_web_camera
from src.camera.settings import WebCameraSettings, exposure_controls_from_env
from src.config import BITRATE, GAP_MS, REC_DIR, SYNC_EVERY, WEB_HOST, WEB_PORT
from src.log import log
from src.recorder.raw_output import SafeRawOutput
from src.recorder.recorder import Recorder, RecordSettings
from src.stream.mjpeg import FrameBroadcaster
from src.web.app import create_app


def main() -> None:
    s = WebCameraSettings()
    picam2 = open_web_camera(s, exposure_controls_from_env())
    picam2.start()

    frames = FrameBroadcaster()
    preview = MJPEGEncoder()
    picam2.start_encoder(preview, FileOutput(frames), name="lores")

    rec = Recorder(
        picam2,
        RecordSettings(rec_dir=str(REC_DIR), fps=s.fps, gap_ms=GAP_MS),
        make_encoder=lambda: H264Encoder(bitrate=BITRATE, repeat=True, iperiod=s.fps),
        make_output=lambda vid, pts: SafeRawOutput(vid, pts, SYNC_EVERY, GAP_MS),
    )
    log(f"웹 서버 시작 http://{WEB_HOST}:{WEB_PORT}/  (미리보기 {s.preview_size}, 녹화 {s.size}@{s.fps})")

    try:
        create_app(rec, frames).run(host=WEB_HOST, port=WEB_PORT, threaded=True)
    finally:
        rec.stop()
        picam2.stop_encoder(preview)
        picam2.stop()
        picam2.close()
        log("웹 서버 종료")
