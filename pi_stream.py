import socket, struct, io, time
from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput
from libcamera import Transform

PORT = 5001


class SockOutput(io.BufferedIOBase):
    def __init__(self, conn, flag):
        self.conn = conn
        self.flag = flag

    def write(self, buf):
        try:
            self.conn.sendall(struct.pack("!I", len(buf)) + buf)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.flag[0] = False


srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("0.0.0.0", PORT))
srv.listen(1)
print(f"대기 중... (포트 {PORT})")

while True:
    conn, addr = srv.accept()
    print(f"접속: {addr}")

    picam2 = None
    try:
        picam2 = Picamera2()
        picam2.configure(picam2.create_video_configuration(
            main={"size": (1280, 720)},
            transform=Transform(hflip=1, vflip=1),   # 180도 회전
        ))

        flag = [True]
        picam2.start_recording(JpegEncoder(), FileOutput(SockOutput(conn, flag)))

        while flag[0]:
            time.sleep(0.1)

    except Exception as e:
        print(f"오류: {e}")

    finally:
        if picam2 is not None:
            try:
                picam2.stop_recording()
            except Exception:
                pass
            picam2.close()
            print("카메라 해제")
        try:
            conn.close()
        except Exception:
            pass
        print("연결 종료, 대기 중...")
