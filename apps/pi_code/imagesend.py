#!/usr/bin/env python3
"""
파이(기체): 인터벌 이미지 캡처 → JPEG 인코딩 → UDP 청크 전송
"""

import io
import time
import socket
import struct
from datetime import datetime

from picamera2 import Picamera2
from PIL import Image

# ===== 설정 =====
DEST_IP = "192.168.0.177"     # 지상국(노트북) IP - 실제 IP로 변경
DEST_PORT = 9999
INTERVAL_SEC = 2.0           # 촬영 간격
NUM_SHOTS = None             # None이면 무한
RESOLUTION = (1920, 1080)
JPG_QUALITY = 80             # 전송량 줄이려 약간 낮춤
CHUNK_SIZE = 60000           # UDP 페이로드 청크 (64KB 미만)

# 헤더 포맷: 이미지ID(4) + 총청크수(2) + 청크인덱스(2) = 8바이트
HEADER_FMT = "!IHH"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

# ===== 초기화 =====
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

picam2 = Picamera2()
config = picam2.create_still_configuration(
    main={"size": RESOLUTION, "format": "RGB888"}
)
picam2.configure(config)
picam2.start()
time.sleep(2)  # 센서 안정화

print(f"전송 시작 → {DEST_IP}:{DEST_PORT}")

img_id = 0
try:
    while NUM_SHOTS is None or img_id < NUM_SHOTS:
        # 캡처
        frame = picam2.capture_array()
        frame = frame[:, :, ::-1]   # BGR→RGB (피부색 보정)
        img = Image.fromarray(frame)

        # JPEG로 메모리 인코딩
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=JPG_QUALITY)
        data = buf.getvalue()

        # 청크 분할
        total_chunks = (len(data) + CHUNK_SIZE - 1) // CHUNK_SIZE

        for idx in range(total_chunks):
            chunk = data[idx * CHUNK_SIZE : (idx + 1) * CHUNK_SIZE]
            header = struct.pack(HEADER_FMT, img_id, total_chunks, idx)
            sock.sendto(header + chunk, (DEST_IP, DEST_PORT))
            time.sleep(0.001)  # 순간 폭주로 인한 유실 완화

        print(f"[img {img_id}] {len(data)} bytes, {total_chunks} chunks 전송")
        img_id += 1
        time.sleep(INTERVAL_SEC)

except KeyboardInterrupt:
    print("\n사용자 중단")

finally:
    picam2.stop()
    sock.close()
    print(f"완료: 총 {img_id}장 전송")
