from __future__ import annotations

from flask import Flask, Response, jsonify, render_template

from src.control.protocol import Controllable, handlers_for
from src.stream.mjpeg import BOUNDARY, FrameBroadcaster, multipart_frames


def create_app(recorder: Controllable, frames: FrameBroadcaster) -> Flask:
    """녹화기와 프레임 배포기를 받아 라우트를 붙인다. picamera2 를 모르므로 가짜로 테스트할 수 있다."""
    app = Flask(__name__)       # 템플릿은 이 파일 옆 templates/
    commands = {k: v for k, v in handlers_for(recorder).items() if k != "status"}

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/stream.mjpg")
    def stream():
        return Response(multipart_frames(frames),
                        mimetype=f"multipart/x-mixed-replace; boundary={BOUNDARY}")

    @app.get("/api/status")
    def status():
        return jsonify(recorder.status())

    @app.post("/api/record/<cmd>")
    def record(cmd: str):
        fn = commands.get(cmd)
        if fn is None:
            return jsonify({"ok": False, "msg": f"알 수 없는 명령: {cmd}"}), 404
        return jsonify(fn())

    return app
