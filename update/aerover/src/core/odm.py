"""NodeODM 제출 · 진행률 폴링 · 취소 · 결과 받기 — 콘티 3절 ③단계.

처리에 약 29분이 든다. 그래서 이 모듈의 존재 이유는 "제출"이 아니라
**진행률·취소·기존 결과 불러오기**다 (콘티 3절이 필수로 꼽은 항목).

    OdmJob      순서만 안다 — 제출 → 대기 → 내려받기 → 꺼내기. pyodm 을 쓰고 Qt 를 모른다
    OdmWorker   OdmJob 을 QThread 에서 돌리고 진행·결과를 시그널로 바꾼다

두 가지 모드로 쓴다.
  · 새로 제출  — `OdmWorker(images=[...], dest=...)`
  · 기존 결과  — `OdmWorker(uuid="8c13e838-...", dest=...)`   제출을 건너뛰고 바로 내려받는다

둘 다 끝나면 회차 폴더에 두 파일이 떨어진다 (`ASSETS`).
  `model.glb`        ⑤ 뷰포트가 띄운다
  `point_cloud.laz`  ④ 배경 제거의 평면도 원본 후보

**`odm_filterpoints/point_cloud.ply` 는 all.zip 에 들어 있지 않다.** 컨테이너
디스크에는 있지만 NodeODM 이 기본 묶음에서 뺀다. 대신
`odm_georeferencing/odm_georeferenced_model.laz` 가 들어 있고 **좌표계가 같다**
(1차-B 경계 상자 x -13.03~1.84 가 LAZ 범위 x -66~48 안에 들어간다). 그래서 이걸 받는다.
"""
from __future__ import annotations

import re
import shutil
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from PySide6.QtCore import QThread, Signal
from pyodm import Node
from pyodm.exceptions import NodeResponseError, OdmError, TaskFailedError
from pyodm.types import TaskStatus

from src.config import ODM_HOST, ODM_PORT

POLL_MS = 3000
CANCEL_CHECK_MS = 200      # 취소 반응이 POLL_MS 만큼 늦지 않도록 잘게 쪼개 잔다
STAGE_CHARS = 34           # 350px 패널에서 한 줄에 들어가는 길이
EXPECTED_MIN = 29          # 112장 · M5/10코어/10.4GB 실측
UPLOAD_SHARE = 0.05        # 업로드는 전체 진행률의 앞 5% 로 눌러 담는다

# 로그 앞머리의 타임스탬프·로그레벨은 화면에 쓸모가 없다
_STRIP_PREFIX = re.compile(
    r"^(?:\d{4}-\d\d-\d\d \d\d:\d\d:\d\d[,.]\d+\s+)?"
    r"(?:\[?(?:INFO|DEBUG|WARNING|ERROR)\]?:?\s+)?")

# **1차·1차-B 가 통과한 바로 그 옵션 조합이다.** 임의로 바꾸지 않는다.
# `gltf` 가 없으면 GLB 가 안 나오고 obj 만 떨어진다 — ⑤ 뷰포트가 못 읽는다.
# 옵션을 바꿀 때는 한 번에 하나씩 (콘티 3절, 처리 1회에 29분).
OPTIONS: Mapping[str, object] = {"pc-ept": True, "cog": True, "gltf": True}

# all.zip 안에서 우리가 쓰는 것은 이 둘뿐이다. 나머지 165MB 는 버린다.
# LAZ 는 ③ 이 쓰지 않는다 — ④ 를 위해 지금 꺼내둔다. 처리 1회에 29분이 들어
# 나중에 필요해졌을 때 다시 돌릴 수가 없다.
ASSETS: Mapping[str, str] = {
    "odm_texturing/odm_textured_model_geo.glb": "model.glb",
    "odm_georeferencing/odm_georeferenced_model.laz": "point_cloud.laz",
}


@dataclass
class OdmResult:
    uuid: str
    glb: Path
    cloud: Path


class Cancelled(Exception):
    pass


class ProgressSink(Protocol):
    """작업이 진행 상황을 알리는 통로. 워커는 이걸 시그널로 바꾼다."""
    def on_submitted(self, uuid: str) -> None: ...
    def on_progress(self, pct: float, stage: str) -> None: ...


# ---- 순수 조각 ----

def stage_text(info, expected_min: int = EXPECTED_MIN, width: int = STAGE_CHARS) -> str:
    """콘솔 마지막 줄에서 "지금 뭘 하는지"만 뽑는다.

    원문은 `2026-09-05 15:33:24,155 DEBUG: Matching 20260904_...` 처럼 길다.
    350px 패널에 그대로 넣으면 두 줄로 넘쳐 진행률 바를 밀어낸다.
    """
    elapsed = (f"{info.processing_time / 60000:.0f}분 / 약 {expected_min}분"
               if info.processing_time > 0 else "대기 중")
    line = next((s.strip() for s in reversed(info.output or []) if s.strip()), "")
    line = " ".join(_STRIP_PREFIX.sub("", line).split())[:width]
    return f"{line} · {elapsed}" if line else elapsed


def extract_assets(zip_path: Path, dest: Path, assets: Mapping[str, str] = ASSETS) -> None:
    """all.zip 에서 필요한 파일만 꺼낸다. 없는 게 있으면 아무것도 안 꺼내고 OdmError."""
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        missing = [a for a in assets if a not in names]
        if missing:
            raise OdmError(f"결과에 없는 파일: {missing}. gltf 옵션 없이 처리된 작업일 수 있다")
        for src, out in assets.items():
            with z.open(src) as f, open(dest / out, "wb") as g:
                shutil.copyfileobj(f, g)


# ---- 작업 ----

class OdmJob:
    """제출 → 폴링 → 내려받기 → 압축 해제. 취소는 `is_cancelled()` 로 묻는다.

    노드·잠자기·취소 판정을 전부 주입받아 NodeODM 없이도 순서를 테스트할 수 있다.
    """

    def __init__(self, node, dest: Path, sink: ProgressSink,
                 is_cancelled: Callable[[], bool],
                 sleep_ms: Callable[[int], None],
                 images: Sequence[Path] = (), uuid: str | None = None, name: str = "",
                 cameras: Path | None = None,
                 options: Mapping[str, object] = OPTIONS,
                 assets: Mapping[str, str] = ASSETS,
                 poll_ms: int = POLL_MS) -> None:
        self._node = node
        self.dest = dest
        self._sink = sink
        self._is_cancelled = is_cancelled
        self._sleep_ms = sleep_ms
        self.images = list(images)
        self.uuid = uuid
        self.name = name
        self.cameras = cameras           # 내부 파라미터 json. EXIF 없는 수집본에만 준다
        self._options = options
        self._assets = assets
        self._poll_ms = poll_ms
        self._task = None

    def run(self) -> OdmResult:
        resumed = bool(self.uuid)
        self._task = self._node.get_task(self.uuid) if resumed else self._submit()
        self.uuid = self._task.info().uuid
        if not resumed:
            self._sink.on_submitted(self.uuid)
        self._wait(self._task, resumed)
        return self._fetch(self._task)

    def cancel_remote(self) -> None:
        """노드 쪽 작업도 멈춘다. 실패해도 조용히 넘어간다 —
        이미 사용자는 취소를 눌렀고, 여기서 낼 수 있는 조치가 없다."""
        try:
            if self._task is not None:
                self._task.cancel()
        except OdmError:
            pass

    # ---- 단계 ----

    def _submit(self):
        self._sink.on_progress(0.0, f"{len(self.images)}장 업로드 중…")
        return self._node.create_task([str(p) for p in self.images], self._task_options(),
                                      name=self.name or None,
                                      progress_callback=self._on_upload)

    def _task_options(self) -> dict:
        """OPTIONS 에 카메라 내부 파라미터를 얹는다. OPTIONS 자체는 건드리지 않는다.

        `--cameras` 는 파일 경로도 받지만 **NodeODM 은 컨테이너 안이라 호스트 경로가
        안 보인다.** 그래서 JSON 문자열로 실어 보낸다.
        """
        if self.cameras is None:
            return dict(self._options)
        return {**self._options, "cameras": self.cameras.read_text()}

    def _on_upload(self, pct: float) -> None:
        self._sink.on_progress(pct * UPLOAD_SHARE, f"업로드 {pct:.0f}%")

    def _wait(self, task, resumed: bool) -> None:
        """상태를 poll_ms 간격으로 확인한다. 취소는 여기서 걸린다.

        `resumed` 면 노드 쪽 취소는 **사용자의 취소가 아니다** — 지난번에 죽은
        작업을 붙잡은 것이다. 조용히 끝내면 그 회차는 영영 다시 제출할 수 없다.
        """
        while True:
            self._check_cancel()
            # -1 은 **마지막 한 줄만** 준다 (NodeODM 이 JS slice 를 그대로 쓴다).
            # 0 으로 두면 3초마다 로그 전체를 받는다 — 29분이면 수백 회다.
            info = task.info(with_output=-1)

            if info.status is TaskStatus.COMPLETED:
                return
            if info.status is TaskStatus.FAILED:
                raise TaskFailedError(info.last_error or "ODM 처리 실패")
            if info.status is TaskStatus.CANCELED:
                if resumed:
                    raise TaskFailedError("기록된 작업이 노드에서 취소된 상태다")
                raise Cancelled

            self._sink.on_progress(UPLOAD_SHARE * 100 + info.progress * (1 - UPLOAD_SHARE),
                                   stage_text(info))
            self._sleep(self._poll_ms)

    def _fetch(self, task) -> OdmResult:
        """all.zip 을 받아 필요한 파일만 꺼낸다.

        NodeODM 2.2.4 의 `/download/<asset>` 는 **`all.zip` 만 받는다** — 개별 파일
        엔드포인트가 없다. 210MB 지만 로컬이라 0.5초다 (실측 438MB/s).
        제출 시 `outputs` 로 zip 내용을 줄일 수도 있지만, 그러면 이 코드가
        새 제출과 기존 결과에서 다르게 동작한다. 받아서 버리는 편이 단순하다.
        """
        self._check_cancel()
        self.dest.mkdir(parents=True, exist_ok=True)
        self._sink.on_progress(95.0, "결과 내려받는 중…")
        zip_path = Path(task.download_zip(str(self.dest)))
        try:
            self._check_cancel()
            self._sink.on_progress(99.0, "압축 푸는 중…")
            extract_assets(zip_path, self.dest, self._assets)
        finally:
            zip_path.unlink(missing_ok=True)      # 210MB 를 남겨두지 않는다

        self._sink.on_progress(100.0, "완료")
        glb, cloud = (self.dest / out for out in self._assets.values())
        return OdmResult(self.uuid, glb, cloud)

    # ---- 취소 ----

    def _check_cancel(self) -> None:
        if self._is_cancelled():
            raise Cancelled

    def _sleep(self, ms: int) -> None:
        for _ in range(max(1, ms // CANCEL_CHECK_MS)):
            self._check_cancel()
            self._sleep_ms(CANCEL_CHECK_MS)


# ---- Qt 어댑터 ----

class OdmWorker(QThread):
    """OdmJob 을 스레드에서 돌리고, 진행 상황과 결과를 시그널로 내보낸다."""

    submitted = Signal(str)           # uuid — 제출되자마자. 받는 쪽은 즉시 기록할 것
    progress = Signal(float, str)     # (0~100, 지금 무엇을 하는지)
    finished_ok = Signal(object)      # OdmResult
    failed = Signal(str)
    cancelled = Signal()              # 사용자가 취소했다 (노드 쪽 작업도 취소 요청함)

    def __init__(self, dest: Path, images: list[Path] | None = None,
                 uuid: str | None = None, name: str = "",
                 cameras: Path | None = None,
                 host: str = ODM_HOST, port: int = ODM_PORT, parent=None) -> None:
        super().__init__(parent)
        if not images and not uuid:
            raise ValueError("images 나 uuid 중 하나는 있어야 한다")
        self.dest = dest
        self.images = images or []
        self.uuid = uuid
        self.name = name
        self.cameras = cameras
        self.host, self.port = host, port
        self._cancel = threading.Event()
        self._job: OdmJob | None = None

    def cancel(self) -> None:
        """플래그만 세운다. 실제 취소는 스레드 안에서 안전한 지점에 처리한다."""
        self._cancel.set()

    def run(self) -> None:
        job = self._job = OdmJob(Node(self.host, self.port), self.dest, sink=self,
                                 is_cancelled=self._cancel.is_set, sleep_ms=self.msleep,
                                 images=self.images, uuid=self.uuid, name=self.name,
                                 cameras=self.cameras)
        try:
            result = job.run()
        except Cancelled:
            job.cancel_remote()
            self.cancelled.emit()
        except (NodeResponseError, TaskFailedError, OdmError) as e:
            self.failed.emit(f"{type(e).__name__}: {e}")
        except OSError as e:
            self.failed.emit(f"파일 처리 실패 — {e}")
        except Exception as e:          # 깨진 zip 등 — 스레드가 조용히 죽지 않게 한다
            self.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.uuid = result.uuid
            self.finished_ok.emit(result)

    # ProgressSink — 작업 스레드에서 불리고, 시그널이 GUI 스레드로 넘긴다
    def on_submitted(self, uuid: str) -> None:
        self.uuid = uuid
        self.submitted.emit(uuid)

    def on_progress(self, pct: float, stage: str) -> None:
        self.progress.emit(pct, stage)
