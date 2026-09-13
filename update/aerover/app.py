"""AeroVer 지상국 실행 진입점.

    uv run python app.py
"""
import sys

from PySide6.QtWidgets import QApplication

from src.gl.viewport import configure_surface_format
from src.main_window import MainWindow
from src.ui.palette import app_qss


def main() -> int:
    configure_surface_format()      # QApplication 생성 전이어야 한다
    app = QApplication(sys.argv)
    app.setStyleSheet(app_qss())

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
