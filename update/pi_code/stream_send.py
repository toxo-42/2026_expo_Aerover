import socket, struct, time, io
from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

HOST = "192.168.137.1"   # 노트북 핫스팟 IP
PORT = 5001

class SocketOutput(io.BufferedIOBase):
    def __init__(self, sock):
        self.sock = sock
    def write(self, buf):
        self.sock.sendall(struct.pack("!I", len(buf)) + buf)

picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((HOST, PORT))
print("연결됨, 스트리밍 시작")

output = SocketOutput(s)
picam2.start_recording(JpegEncoder(q=70), FileOutput(output))

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n종료")
finally:
    picam2.stop_recording()
    s.close()
