import threading

import pytest

pytest.importorskip("flask")

from src.stream.mjpeg import FrameBroadcaster   # noqa: E402
from src.web.app import create_app              # noqa: E402


class FakeRecorder:
    def __init__(self) -> None:
        self.recording = False

    def start(self):
        self.recording = True
        return {"ok": True, "msg": "녹화 시작", "file": "rec/flight_x.h264"}

    def stop(self):
        self.recording = False
        return {"ok": True, "msg": "녹화 종료"}

    def toggle(self):
        return self.stop() if self.recording else self.start()

    def status(self):
        return {"ok": True, "recording": self.recording}


@pytest.fixture
def client():
    frames = FrameBroadcaster()
    app = create_app(FakeRecorder(), frames)
    app.testing = True
    return app.test_client(), frames


def test_index_and_status(client):
    c, _ = client
    assert c.get("/").status_code == 200
    assert "녹화" in c.get("/").get_data(as_text=True)
    assert c.get("/api/status").get_json() == {"ok": True, "recording": False}


def test_record_commands(client):
    c, _ = client
    assert c.post("/api/record/start").get_json()["ok"] is True
    assert c.get("/api/status").get_json()["recording"] is True
    assert c.post("/api/record/toggle").get_json()["msg"] == "녹화 종료"
    assert c.post("/api/record/status").status_code == 404      # status 는 GET 전용
    assert c.post("/api/record/bogus").status_code == 404


def test_stream_yields_multipart_frames(client):
    c, frames = client
    # 테스트 클라이언트는 응답을 만들며 첫 조각을 미리 읽는다 — 그 전에 프레임이 와 있어야 한다
    threading.Timer(0.05, lambda: frames.write(b"JPG")).start()
    resp = c.get("/stream.mjpg")
    assert resp.mimetype == "multipart/x-mixed-replace"
    first = next(resp.response)
    assert b"Content-Type: image/jpeg" in first and first.endswith(b"JPG\r\n")
    resp.close()
