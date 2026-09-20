"""3D 매핑 페이지 — 콘티 3절.

입력 촬영본을 ODM 에 넣어 3D 형상을 만들고 배경을 제거하기까지의 5단계.
앞 단계가 끝나야 다음이 열린다. 처리에 약 29분이 걸리므로 진행률·취소·
기존 결과 불러오기가 선택이 아니라 필수다.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from src.config import CAMERAS_FILE, MODELS_DIR, SESSIONS_DIR
from src.core import checkcache, cropper, subsample
from src.core.imgcheck import CheckReport, list_images
from src.core.odm import OdmWorker
from src.core.workspace import ModelStore
from src.gl.viewport import MeshViewport
from src.pages.mapping.steps import STEPS, State, StepRow
from src.pages.mapping.workers import CheckWorker, CropWorker
from src.ui.palette import STATUS_OK_TEXT, STATUS_RED, TEXT_DIM
from src.ui.planview import CropDialog

PANEL_WIDTH = 250        # 시안 2절 — 뷰어에 자리를 내준다
# 서브샘플 결과는 `3D_model/<회차>/sub/<타임스탬프>/` 에 쌓인다.
# 실행마다 새 폴더라 이전 선별본을 덮지 않는다 — 어떤 세트로 ODM 을 돌렸는지 남는다.
HUD_MARGIN = 16         # 뷰어 가장자리와 플로팅 버튼 사이 여백
ZOOM_STEP = 2.0         # 버튼 한 번에 휠 두 칸 — 한 칸은 체감이 너무 작다
# (글리프, 툴팁, 누르면 할 일). 이름은 화면에 쓰지 않고 툴팁으로만 보인다.
VIEW_TOOLS = [
    ("＋", "확대", lambda v: v.zoom_by(ZOOM_STEP)),
    ("－", "축소", lambda v: v.zoom_by(-ZOOM_STEP)),
    ("⌂", "탑뷰", lambda v: v.top_view()),
    ("↻", "시점 초기화", lambda v: v.reset_view()),
]
MAX_LOG = 100           # 기록 카드가 무한히 자라지 않게 자른다
SUB_DIRNAME = "sub"
UUID_FILE = "odm_uuid.txt"      # 제출 직후 남긴다 — 29분짜리 작업을 앱을 닫아도 다시 붙잡는다
MODEL_FILE = "model.glb"
CROPPED_FILE = "model_cropped.glb"   # ④ 산출물. 원본을 덮지 않는다 — 경계를 다시 잡을 수 있어야 한다


def _shown(path: Path) -> str:
    """산출물 경로를 `3D_model/<회차>/<파일>` 로 짧게 보여준다."""
    try:
        return str(Path(MODELS_DIR.name) / path.resolve().relative_to(MODELS_DIR))
    except ValueError:
        return path.name


def subsample_detail(sub_dir: Path) -> tuple[str, str]:
    return f"{len(list_images(sub_dir))}장", f"{_shown(sub_dir)}"


class MappingPage(QWidget):
    def __init__(self, sessions_dir: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.sessions_dir = sessions_dir or SESSIONS_DIR

        self.input_dir: Path | None = None
        self.report: CheckReport | None = None
        self.sub_dir: Path | None = None
        self._worker: CheckWorker | None = None
        self._odm: OdmWorker | None = None
        self._crop: CropWorker | None = None
        self._has_sessions = False
        self._store = ModelStore()          # 회차 → 산출물 폴더. source.txt 는 상대경로다

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)
        root.addWidget(self._build_panel())

        self.viewport = MeshViewport()
        self.viewport.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.viewport, 1)

        # 뷰어 위에 떠 있는다 (시안 2절). 레이아웃이 아니라 좌표로 얹는다.
        self.view_tools = self._build_view_tools()
        self.view_tools.raise_()

        self._refresh_sessions()
        self._sync_steps()

    # ---- 좌 패널 ----

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(PANEL_WIDTH)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        row = QHBoxLayout()
        label = QLabel("회차")
        label.setObjectName("dim")
        self.session_box = QComboBox()
        self.session_box.currentIndexChanged.connect(self._on_session_changed)
        row.addWidget(label)
        row.addWidget(self.session_box, 1)
        lay.addLayout(row)

        # 카드가 세로 여유를 똑같이 나눠 갖는다 — 아래에 빈 공간을 남기지 않는다.
        self.steps: dict[str, StepRow] = {}
        for i, spec in enumerate(STEPS):
            step = StepRow(i, spec)
            step.triggered.connect(self._on_step)
            self.steps[spec.key] = step
            lay.addWidget(step)

        # 카드 아래 남는 자리는 기록 카드가 받는다. 카드에서 뺀 긴 글(검사 요약,
        # 경고 목록, 오류, 저장 경로)이 시간순으로 쌓인다 — 빈 공간으로 두지 않는다.
        # 29분짜리 작업이라 "아까 뭐라고 떴었지" 를 되짚을 수 있어야 한다.
        log_box = QFrame()
        log_box.setObjectName("panel")
        log_lay = QVBoxLayout(log_box)
        log_lay.setContentsMargins(12, 10, 12, 10)
        log_lay.setSpacing(6)
        head = QLabel("기록")
        head.setStyleSheet("font-weight:600;")
        log_lay.addWidget(head)
        self.log = QListWidget()
        self.log.setObjectName("log")
        self.log.setFrameShape(QFrame.Shape.NoFrame)
        self.log.setWordWrap(True)
        log_lay.addWidget(self.log, 1)
        lay.addWidget(log_box, 1)

        return panel

    def _build_view_tools(self) -> QFrame:
        """뷰어 우하단 아이콘 버튼 묶음. 이름은 툴팁으로만 보인다."""
        box = QFrame(self)
        box.setObjectName("hudTools")      # 버튼만 보이게 — 묶음 자체는 배경이 없다
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for glyph, tip, action in VIEW_TOOLS:
            btn = QPushButton(glyph, box)
            btn.setObjectName("hudIconButton")
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _=False, a=action: a(self.viewport))
            lay.addWidget(btn)
        return box

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        view = self.viewport.geometry()
        size = self.view_tools.sizeHint()
        self.view_tools.setGeometry(view.right() - HUD_MARGIN - size.width() + 1,
                                    view.bottom() - HUD_MARGIN - size.height() + 1,
                                    size.width(), size.height())

    # ---- 상태 ----

    def _stage_label(self, folder: Path) -> str:
        """그 회차가 어디까지 갔는지 — 산출물 폴더의 **파일**만 보고 판단한다.
        고르기 전에 목록에서 비교할 수 있어야 한다."""
        out = self._store.find(folder)
        if out is None:
            return "미검사"
        if (out / CROPPED_FILE).is_file():
            return "④ 배경 제거"
        if (out / MODEL_FILE).is_file():
            return "③ ODM 완료"
        if (out / UUID_FILE).is_file():
            return "③ 진행 중"
        if (out / SUB_DIRNAME).is_dir():
            return "② 선별"
        return "① 검사" if checkcache.load(folder, out) else "미검사"

    def _refresh_sessions(self) -> None:
        """항목 글자는 `회차 — 단계`, 실제 폴더명은 항목 데이터로 들고 있는다."""
        self.session_box.clear()
        if self.sessions_dir.is_dir():
            names = sorted((d.name for d in self.sessions_dir.iterdir() if d.is_dir()), reverse=True)
        else:
            names = []
        self._has_sessions = bool(names)
        for name in names:
            self.session_box.addItem(f"{name}  —  {self._stage_label(self.sessions_dir / name)}",
                                     name)
        if not names:
            self.session_box.addItem("회차 없음", None)
        self.session_box.setEnabled(self._has_sessions and not self._busy())

    def _update_session_label(self) -> None:
        """지금 고른 회차의 단계 표시를 다시 적는다. 항목 데이터는 그대로라
        currentIndexChanged 가 다시 돌지 않는다."""
        i = self.session_box.currentIndex()
        name = self.session_box.itemData(i)
        if name:
            self.session_box.setItemText(
                i, f"{name}  —  {self._stage_label(self.sessions_dir / name)}")

    def _on_session_changed(self, index: int) -> None:
        """회차를 고르면 ① 의 입력 폴더가 그 회차가 된다.

        검사를 자동으로 걸지는 않는다 — 446장에 약 46초가 들어 사용자가 눌러야 한다.
        """
        if not self.session_box.isEnabled():
            return
        name = self.session_box.itemData(index)
        folder = self.sessions_dir / name if name else None
        if folder is not None and folder.is_dir():
            self._set_input(folder)

    def _set_input(self, folder: Path) -> None:
        """입력 폴더를 바꾼다. 앞선 검사 결과는 무효가 되므로 ②부터 다시 잠근다.

        다만 **이 촬영본으로 이미 만들어 둔 선별본이 있으면 가장 최근 것을 집는다.**
        이 페이지는 판단 근거를 메모리가 아니라 파일에 두기로 했고(`_sync_odm`),
        그래야 앱을 닫았다 켜도 ③ 이 바로 열린다.
        """
        self.input_dir = folder
        self.sub_dir = self._latest_sub()
        n = len(list_images(folder))

        # 앞서 검사해 둔 결과가 있으면 되살린다 — 사진이 그대로일 때만 유효하다.
        # 446장 재검사에 46초가 들고, ② 는 그 결과의 블러 값이 있어야 돌아간다.
        out = self._out_dir()
        self.report = checkcache.load(folder, out) if out else None
        if self.report is not None:
            self.steps["input"].set_state(State.DONE)
            self.steps["input"].set_detail("검사 완료", self.report.summary())
            self._note(f"{folder.name} — 지난 검사 결과를 불러왔다 ({self.report.summary()})")
        else:
            self.steps["input"].set_state(State.READY)
            self.steps["input"].set_detail(f"{n}장", str(folder))
        self._sync_steps()

    def _sync_steps(self) -> None:
        """앞 단계가 끝나야 다음이 열린다.

        ⑤ 는 예외로 항상 열어둔다. 처리 1회에 약 29분이 들기 때문에
        기존 결과를 바로 불러올 수 있어야 한다 (콘티 3절).
        """
        # 스레드가 도는 동안 회차를 바꾸면 결과가 엉뚱한 폴더를 가리키게 된다.
        # ③ 은 29분이 걸려 그 사이에 만질 여지가 충분하다.
        self.session_box.setEnabled(self._has_sessions and not self._busy())

        if self.input_dir is None:
            self.steps["input"].set_state(State.READY, "")

        # 검사에 경고가 있으면 ② 를 열지 않는다. 처리에 29분이 드는데
        # 입력이 나쁘면 그대로 버려진다.
        passed = self.report is not None and self.report.ok
        sub = self.steps["subsample"]
        sub.set_state(State.DONE if self.sub_dir else State.READY if passed else State.LOCKED)
        if self.sub_dir:
            sub.set_detail(*subsample_detail(self.sub_dir))
        else:
            sub.set_detail("")                 # 잠기면 이전 결과 문구를 지운다

        self._sync_odm()
        self._sync_crop()
        self._update_session_label()
        if self.steps["save"].state is not State.DONE:
            self.steps["save"].set_state(State.READY)

    def _sync_odm(self) -> None:
        """③ 은 세 갈래다 — 이미 결과가 있나 / 이어붙일 작업이 있나 / 새로 제출하나.

        판단 근거를 메모리가 아니라 **회차 폴더의 파일**에 둔다. 처리에 29분이 들어
        앱을 닫았다 켜도 이어져야 하기 때문이다.
        """
        step = self.steps["odm"]
        # 도는 중에는 진행률 표시를 건드리지 않는다. 판단 기준은 **워커의 존재**다 —
        # 위젯 상태로 보면 취소·실패 뒤에도 RUNNING 에 갇힌다 (`_on_odm_finished`
        # 이 워커를 먼저 지우므로 그때는 여기를 통과해야 한다).
        if self._odm is not None:
            return

        if self._model_path() and self._model_path().is_file():
            step.set_state(State.DONE)
            step.set_detail(self._model_path().name, str(self._model_path()))
        elif self._saved_uuid():
            step.set_state(State.READY)
            step.set_detail("결과 받기", f"작업 {self._saved_uuid()[:8]} 의 결과를 가져온다")
        elif self.sub_dir:
            step.set_state(State.READY)
            step.set_detail(f"{len(list_images(self.sub_dir))}장", "제출하면 약 29분 걸린다")
        else:
            step.set_state(State.LOCKED)
            step.set_detail("")

    def _sync_crop(self) -> None:
        """④ 는 ③ 의 산출물이 있어야 열린다."""
        step = self.steps["crop"]
        if self._crop is not None:
            return
        if self._cropped_path() and self._cropped_path().is_file():
            step.set_state(State.DONE)
            step.set_detail(self._cropped_path().name, str(self._cropped_path()))
        elif self._model_path() and self._model_path().is_file():
            step.set_state(State.READY)
            step.set_detail("")
        else:
            step.set_state(State.LOCKED)
            step.set_detail("")

    def _latest_sub(self) -> Path | None:
        """이 촬영본의 가장 최근 선별본. 폴더명이 타임스탬프라 이름순 정렬이면 된다."""
        d = self._out_dir()
        root = d / SUB_DIRNAME if d else None
        if root is None or not root.is_dir():
            return None
        dirs = sorted((x for x in root.iterdir() if x.is_dir() and list_images(x)),
                      key=lambda x: x.name)
        return dirs[-1] if dirs else None

    def _out_dir(self, create: bool = False) -> Path | None:
        """이 촬영본의 산출물이 모이는 곳 — `3D_model/<입력폴더명>/`.

        회차마다 하위 폴더를 나눈다. 한 폴더에 몰면 다음 실행이 이전 것을 덮고,
        무엇보다 **회차마다 좌표계가 달라 섞으면 안 된다** (콘티 2절).

        폴더명이 겹칠 수 있어(`sub` 같은 흔한 이름) `source.txt` 에 원본 경로를 적고,
        같은 이름이 다른 원본에서 왔으면 `_2`, `_3` 을 붙인다. 찾기·만들기는 `core.workspace`.
        """
        if self.input_dir is None:
            return None
        return self._store.dir_for(self.input_dir, create)

    def _cropped_path(self) -> Path | None:
        d = self._out_dir()
        return d / CROPPED_FILE if d else None

    def _busy(self) -> bool:
        return any(w is not None for w in (self._worker, self._odm, self._crop))

    def _model_path(self) -> Path | None:
        d = self._out_dir()
        return d / MODEL_FILE if d else None

    def _saved_uuid(self) -> str:
        """앞서 제출한 작업의 uuid. 없으면 빈 문자열."""
        if self.input_dir is None:
            return ""
        d = self._out_dir()
        f = d / UUID_FILE if d else None
        return f.read_text().strip() if f and f.is_file() else ""

    # ---- 동작 ----

    def _on_step(self, key: str) -> None:
        if self.steps[key].state is State.RUNNING:
            self._cancel_step(key)
        elif key == "input":
            self._pick_input()
        elif key == "subsample":
            self._run_subsample()
        elif key == "odm":
            self._run_odm()
        elif key == "crop":
            self._run_crop()
        elif key == "save":
            self._pick_model()
        else:
            self._note(f"'{self.steps[key].spec.title}' 은 아직 연결되지 않았다.")

    def _cancel_step(self, key: str) -> None:
        if key == "input":
            self._cancel_check()
        elif key == "odm" and self._odm is not None:
            self._odm.cancel()
            self._note("ODM 작업을 취소하는 중…", TEXT_DIM)

    # ---- ① 입력 검사 ----

    def _pick_input(self) -> None:
        start = str(self.input_dir or self.sessions_dir)
        folder = QFileDialog.getExistingDirectory(self, "촬영본 폴더 선택", start)
        if not folder:
            return
        self._set_input(Path(folder))
        self._start_check()

    def _start_check(self) -> None:
        if self.input_dir is None or self._worker is not None:
            return
        step = self.steps["input"]
        step.set_state(State.RUNNING)
        step.set_detail("검사 중…", str(self.input_dir))
        step.set_progress(0, 1)
        self._note("장수·해상도·EXIF·블러·인접매칭을 확인한다.", TEXT_DIM)

        self._worker = CheckWorker(self.input_dir, self)
        self._worker.progress.connect(self._on_check_progress)
        self._worker.finished_ok.connect(self._on_check_done)
        self._worker.finished.connect(self._on_check_finished)
        self._worker.start()
        self.session_box.setEnabled(False)

    def _cancel_check(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._note("검사를 취소했다.", TEXT_DIM)

    def _on_check_progress(self, done: int, total: int) -> None:
        step = self.steps["input"]
        step.set_progress(done, total)
        step.set_detail(f"{done}/{total}")

    def _on_check_done(self, report: CheckReport) -> None:
        self.report = report
        if report.ok:
            self.steps["input"].set_state(State.DONE)
            # 참고는 차단하지 않지만 보이긴 해야 한다 — EXIF 없음이 툴팁에 뜬다.
            tip = "\n".join([report.summary(), *(f"- {t}" for t in report.notes)])
            self.steps["input"].set_detail("검사 완료", tip)
            checkcache.save(report, self._out_dir(create=True))
            self._note(f"검사 통과 — {report.summary()}", STATUS_OK_TEXT)
        else:
            # 경고는 통과 못 한 것이다. ① 은 다시 실행할 수 있게 READY 로 둔다.
            self.steps["input"].set_state(State.READY)
            self.steps["input"].set_detail(f"경고 {len(report.warnings)}건",
                                          "\n".join(f"! {w}" for w in report.warnings))
            self._note(f"경고 {len(report.warnings)}건 — ODM 에 넣기 전에 재촬영할 것.", STATUS_RED)

    def _on_check_finished(self) -> None:
        """취소·완료 어느 쪽이든 스레드가 끝나면 불린다."""
        if self.report is None and self.input_dir is not None:
            self._set_input(self.input_dir)      # 취소 — 검사 전 상태로 되돌린다
        self._worker = None
        self._sync_steps()

    # ---- ② 서브샘플 ----

    def _run_subsample(self) -> None:
        """4장 묶음마다 가장 선명한 한 장. 블러는 ① 이 잰 값을 그대로 쓴다 —
        446장 라플라시안에 40초가 넘게 들어 다시 재지 않는다."""
        if self.report is None:
            # 디스크에서 집어온 선별본만 있고 이번 세션에 ① 을 안 돌린 상태다.
            self._note("먼저 ① 입력 검사를 돌려야 한다 — 블러 값이 있어야 고를 수 있다.",
                       STATUS_RED)
            return
        picked = subsample.select(self.report.stats)
        dst = (self._out_dir(create=True) / SUB_DIRNAME
               / datetime.now().strftime("%Y%m%d_%H%M%S"))
        try:
            subsample.copy_to(picked, dst)
        except OSError as e:
            self._note(f"서브샘플 저장 실패 — {e}", STATUS_RED)
            return
        self.sub_dir = dst
        self._note(subsample.summary(self.report.stats, picked), STATUS_OK_TEXT)
        self._sync_steps()

    # ---- ③ ODM 제출 ----

    def _run_odm(self) -> None:
        """제출하거나, 앞서 제출한 작업에 다시 붙는다.

        `_saved_uuid()` 가 있으면 제출을 건너뛴다 — 같은 회차를 두 번 돌리면
        29분을 그냥 버리는 것이다.
        """
        if self.input_dir is None or self._odm is not None:
            return
        uuid = self._saved_uuid()
        if not uuid and not self.sub_dir:
            return

        step = self.steps["odm"]
        step.set_state(State.RUNNING)
        step.set_progress(0, 100)
        self._note("NodeODM 에 연결하는 중…", TEXT_DIM)

        no_exif = self.report is not None and not any(s.focal for s in self.report.stats)
        self._odm = OdmWorker(dest=self._out_dir(create=True),
                              images=None if uuid else list_images(self.sub_dir),
                              uuid=uuid or None,
                              name=self.input_dir.name,
                              cameras=CAMERAS_FILE if no_exif else None, parent=self)
        self._odm.submitted.connect(self._on_odm_submitted)
        self._odm.progress.connect(self._on_odm_progress)
        self._odm.finished_ok.connect(self._on_odm_done)
        self._odm.failed.connect(self._on_odm_failed)
        self._odm.finished.connect(self._on_odm_finished)
        self._odm.start()
        self.session_box.setEnabled(False)

    def _on_odm_submitted(self, uuid: str) -> None:
        """uuid 를 즉시 회차 폴더에 적는다. 이게 없으면 앱을 닫는 순간
        29분짜리 작업을 다시 찾을 방법이 없다.

        `self.input_dir` 가 아니라 **워커가 들고 있는 dest** 에 적는다 —
        29분 사이에 사용자가 회차를 바꿔도 uuid 는 제출한 회차에 남아야 한다.
        """
        (self._odm.dest / UUID_FILE).write_text(uuid + "\n")

    def _on_odm_progress(self, pct: float, stage: str) -> None:
        step = self.steps["odm"]
        step.set_progress(int(pct), 100)
        # 카드에는 진행률만. 단계 이름은 길어서 툴팁과 아래 상태줄로 보낸다.
        step.set_detail(f"{pct:.0f}%", stage)

    def _on_odm_done(self, result) -> None:
        self.steps["odm"].set_state(State.DONE)
        self.steps["odm"].set_detail(result.glb.name, str(result.glb))
        self._note(f"ODM 완료 — {result.uuid[:8]} · 저장: {_shown(result.glb)}", STATUS_OK_TEXT)
        self.load_model_file(str(result.glb))       # 끝나면 바로 띄운다

    def _on_odm_failed(self, msg: str) -> None:
        # 노드에 남은 작업 자체가 못 쓰는 상태면 기록을 지운다. 안 그러면 그 회차는
        # 계속 죽은 작업에 다시 붙기만 하고 영영 재제출되지 않는다.
        stale = self._odm.dest / UUID_FILE      # self.input_dir 은 그 사이 바뀌었을 수 있다
        if msg.startswith("TaskFailedError") and stale.is_file():
            stale.unlink()
            msg += " — 기록을 지웠다. 다시 누르면 새로 제출한다."
        self._note(msg, STATUS_RED)

    def _on_odm_finished(self) -> None:
        self._odm = None
        self._sync_steps()

    # ---- ④ 배경 제거 ----

    def _run_crop(self) -> None:
        """평면도를 띄워 경계 상자를 받고, 그 상자로 메시를 자른다.

        경계값은 **회차마다 다시 잡아야 한다** — GPS 없이 재구성하면 ODM 좌표계가
        회차마다 임의다. 그래서 코드에 박지 않고 매번 사람이 지정한다 (콘티 3절).
        """
        src = self._model_path()
        if src is None or not src.is_file() or self._crop is not None:
            return

        self._note("모델을 읽는 중…", TEXT_DIM)
        try:
            scene = cropper.load_scene(src)
            xyz = cropper.world_vertices(scene)
        except Exception as e:
            self._note(f"모델을 읽지 못했다 — {e}", STATUS_RED)
            return

        dialog = CropDialog(xyz, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._note("배경 제거를 취소했다.", TEXT_DIM)
            return

        lo, hi, plane = dialog.bounds()
        step = self.steps["crop"]
        step.set_state(State.RUNNING)
        step.set_detail("자르는 중…")
        step.bar.setRange(0, 0)       # 진행률을 알 수 없다 — 불확정 막대로 둔다

        self._crop = CropWorker(scene, self._out_dir(create=True) / CROPPED_FILE,
                                lo, hi, plane, self)
        self._crop.finished_ok.connect(self._on_crop_done)
        self._crop.failed.connect(lambda m: self._note(m, STATUS_RED))
        self._crop.finished.connect(self._on_crop_finished)
        self._crop.start()
        self.session_box.setEnabled(False)

    def _on_crop_done(self, result) -> None:
        self.steps["crop"].set_state(State.DONE)
        self.steps["crop"].set_detail(result.out.name, str(result.out))
        self._note(f"삼각형 {result.faces_before:,} → {result.faces_after:,} "
                   f"({result.ratio * 100:.1f}%) · 저장: {_shown(result.out)}", STATUS_OK_TEXT)
        self.load_model_file(str(result.out))     # 자른 결과를 바로 띄운다

    def _on_crop_finished(self) -> None:
        self.steps["crop"].bar.setRange(0, 100)
        self._crop = None
        self._sync_steps()

    # ---- ⑤ 결과 ----

    def _pick_model(self) -> None:
        start = str(self._out_dir() or MODELS_DIR)
        path, _ = QFileDialog.getOpenFileName(self, "모델 파일 선택", start,
                                              "3D 모델 (*.glb *.gltf)")
        if path:
            self.load_model_file(path)

    def load_model_file(self, path: str) -> bool:
        """뷰포트에 모델을 띄운다. 회차에서 자동으로 부를 수 있게 분리해 뒀다."""
        try:
            model = self.viewport.load_model(path)
        except Exception as e:
            self._note(f"불러오기 실패 — {e}", STATUS_RED)
            return False
        self._note(f"{Path(path).name} — 파트 {len(model.parts)} · "
                   f"면 {model.face_count:,} · {model.load_sec:.2f}초", STATUS_OK_TEXT)
        self.steps["save"].set_state(State.DONE, Path(path).name)
        return True

    # ---- 공용 ----

    def _note(self, text: str, color: str = TEXT_DIM) -> None:
        """기록 카드 맨 위에 쌓는다. 진행률처럼 자주 바뀌는 값은 넣지 않는다 —
        다른 글이 순식간에 밀려난다."""
        item = QListWidgetItem(f"{datetime.now():%H:%M}  {text}")
        item.setForeground(QColor(color))
        self.log.insertItem(0, item)
        while self.log.count() > MAX_LOG:
            self.log.takeItem(self.log.count() - 1)

    def shutdown(self) -> None:
        """MainWindow.closeEvent 규약. QStackedWidget 안의 위젯은 closeEvent 를
        받지 못해, 이게 없으면 검사 스레드가 남는다 (콘티 15절에서 잡은 버그)."""
        for w in (self._worker, self._odm, self._crop):
            if w is not None:
                w.cancel()
                w.wait(3000)
        self._worker = self._odm = self._crop = None
