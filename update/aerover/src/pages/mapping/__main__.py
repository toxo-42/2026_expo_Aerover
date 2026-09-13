"""단독 확인 — 매핑 페이지만 띄운다.

    uv run python -m src.pages.mapping [--shot out.png] [--demo]
"""
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from src.gl.viewport import configure_surface_format
from src.pages.mapping.page import MappingPage
from src.pages.mapping.steps import State
from src.ui.palette import app_qss

argv = sys.argv[1:]
shot = None
if "--shot" in argv:
    i = argv.index("--shot")
    shot = argv[i + 1]
    del argv[i:i + 2]
demo = "--demo" in argv

configure_surface_format()
app = QApplication([])
app.setStyleSheet(app_qss())

page = MappingPage()
page.resize(1180, 760)
page.setWindowTitle("AeroVer — 3D 매핑")
page.show()

if demo:
    # 네 가지 상태를 한 화면에 늘어놓아 시각적으로 비교한다
    page.steps["input"].set_state(State.DONE, "images/ · 446장 · 검사 통과")
    page.steps["subsample"].set_state(State.DONE, "446장 → 112장 (블러 중앙값 109.1)")
    page.steps["odm"].set_state(State.RUNNING, "opensfm: 특징점 매칭 중… 12분 / 약 29분")
    page.steps["odm"].set_progress(41)
    page.steps["crop"].set_state(State.READY)
    page.status.setText("sessions/20260905_191915/")

if shot:
    def grab():
        page.grab().save(shot)
        print(f"저장: {shot}")
        app.quit()
    QTimer.singleShot(1200, grab)

sys.exit(app.exec())
