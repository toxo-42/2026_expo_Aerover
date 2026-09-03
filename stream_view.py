import socket, struct, cv2, numpy as np, time, os
from datetime import datetime

# ===== 경로 설정 (run_mapping.py 와 동일한 상대경로 기준) =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(BASE_DIR, "received")   # 저장 폴더 (코드 위치 기준)

PI_IP = "192.168.137.6"
PORT = 5001
INTERVAL = 3.0                                  # 저장 주기(초)

os.makedirs(SAVE_DIR, exist_ok=True)


def recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
conn.connect((PI_IP, PORT))
print("연결됨:", PI_IP)

last_save = 0.0
count = 0

try:
    while True:
        hdr = recv_exact(conn, 4)
        if not hdr:
            break
        size = struct.unpack("!I", hdr)[0]
        data = recv_exact(conn, size)
        if data is None:
            break

        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue

        now = time.time()
        if now - last_save >= INTERVAL:
            path = os.path.join(SAVE_DIR, f"{count:03d}.jpg")
            with open(path, "wb") as f:
                f.write(data)          # 받은 JPEG 원본 그대로 저장
            print("저장:", path)
            last_save = now
            count += 1

        cv2.imshow("stream", img)
        if cv2.waitKey(1) == 27:
            break
        if cv2.getWindowProperty("stream", cv2.WND_PROP_VISIBLE) < 1:
            break
finally:
    conn.close()
    cv2.destroyAllWindows()
    print(f"총 {count}장 저장")