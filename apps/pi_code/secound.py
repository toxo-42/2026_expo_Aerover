#!/usr/bin/env python3
"""
동영상 녹화 → mp4 저장
picamera2 + H264 인코더
"""

import time
from datetime import datetime
from pathlib import Path

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FfmpegOutput

# ===== 설정 =====
DURATION_SEC = 30           # 녹화 시간 (초). 무한이면 None
OUTPUT_DIR = Path("videos")
RESOLUTION = (1920, 1080)
FPS = 30
BITRATE = 10_000_000        # 10 Mbps

# ===== 초기화 =====
OUTPUT_DIR.mkdir(exist_ok=True)

picam2 = Picamera2()
config = picam2.create_video_configuration(
    main={"size": RESOLUTION, "format": "RGB888"},
    controls={"FrameRate": FPS},
)
picam2.configure(config)

encoder = H264Encoder(bitrate=BITRATE)

# 파일명: 타임스탬프
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
filename = OUTPUT_DIR / f"vid_{ts}.mp4"
output = FfmpegOutput(str(filename))

# ===== 녹화 =====
print(f"녹화 시작: {filename}")
picam2.start_recording(encoder, output)

try:
    if DURATION_SEC is None:
        print("녹화 중... Ctrl+C로 중단")
        while True:
            time.sleep(1)
    else:
        time.sleep(DURATION_SEC)

except KeyboardInterrupt:
    print("\n사용자 중단")

finally:
    picam2.stop_recording()
    print(f"완료: {filename.resolve()}")
