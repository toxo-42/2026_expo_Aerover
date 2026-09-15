# imagesend_tcp.py
import socket, struct, time
from picamera2 import Picamera2

HOST = "192.168.0.177"   # 노트북 IP
PORT = 5001
TARGET_SHOTS = 30
INTERVAL = 1.0

picam2 = Picamera2()
picam2.configure(picam2.create_still_configuration(main={"size": (1920, 1080)}))
picam2.start()
time.sleep(2)

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((HOST, PORT))

for i in range(TARGET_SHOTS):
    path = f"/tmp/shot_{i:03d}.jpg"
    picam2.capture_file(path)
    with open(path, "rb") as f:
        data = f.read()
    # [4바이트 길이][JPEG 바이트]
    s.sendall(struct.pack("!I", len(data)) + data)
    print(f"sent {i} ({len(data)} bytes)")
    time.sleep(INTERVAL)

s.close()
picam2.stop()
