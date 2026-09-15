#!/usr/bin/env python3
"""
인터벌 사진 촬영 → PIL로 JPG 저장
picamera2로 프레임 캡처, PIL로 후처리/저장
"""

import time
from datetime import datetime
from pathlib import Path

from picamera2 import Picamera2
from PIL import Image

# ===== 설정 =====
INTERVAL_SEC = 3.0          # 촬영 간격 (초)
NUM_SHOTS = 20              # 총 촬영 장수 (무한이면 None)
OUTPUT_DIR = Path("captures")  # 저장 폴더
JPG_QUALITY = 90           # JPG 품질 (1~95)
RESOLUTION = (1920, 1080)  # 해상도

# ===== 초기화 =====
OUTPUT_DIR.mkdir(exist_ok=True)

picam2 = Picamera2()
config = picam2.create_still_configuration(
    main={"size": RESOLUTION, "format": "RGB888"}
)
picam2.configure(config)
picam2.start()

# 센서 안정화 대기 (자동노출/화이트밸런스 수렴)
time.sleep(2)
print(f"촬영 시작: {INTERVAL_SEC}초 간격, "
      f"{'무한' if NUM_SHOTS is None else NUM_SHOTS}장")

# ===== 촬영 루프 =====
count = 0
try:
    while NUM_SHOTS is None or count < NUM_SHOTS:
        # 프레임 캡처 (numpy 배열, RGB)
        frame = picam2.capture_array()

        frame = frame[:, :, ::-1]   # BGR → RGB 채널 순서 뒤집기
        img = Image.fromarray(frame)

        # 파일명: 타임스탬프 + 순번
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = OUTPUT_DIR / f"img_{ts}_{count:04d}.jpg"

        # JPG 저장
        img.save(filename, "JPEG", quality=JPG_QUALITY)

        count += 1
        print(f"[{count}] 저장됨: {filename}")

        time.sleep(INTERVAL_SEC)

except KeyboardInterrupt:
    print("\n사용자 중단")

finally:
    picam2.stop()
    print(f"완료: 총 {count}장 저장 → {OUTPUT_DIR.resolve()}")
