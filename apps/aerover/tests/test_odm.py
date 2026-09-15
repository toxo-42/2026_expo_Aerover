import json
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("pyodm")

from pyodm.exceptions import OdmError, TaskFailedError    # noqa: E402
from pyodm.types import TaskStatus                        # noqa: E402

from src.core import odm                                  # noqa: E402
from src.core.odm import ASSETS, Cancelled, OdmJob, extract_assets, stage_text   # noqa: E402


class Info:
    def __init__(self, uuid, status, progress=0.0, processing_time=0, output=(), last_error=""):
        self.uuid, self.status, self.progress = uuid, status, progress
        self.processing_time, self.output, self.last_error = processing_time, list(output), last_error


class FakeTask:
    def __init__(self, uuid: str, timeline: list[TaskStatus]) -> None:
        self.uuid = uuid
        self._timeline = list(timeline)
        self.cancelled = False
        self.info_calls: list = []

    def info(self, with_output=None):
        self.info_calls.append(with_output)
        status = self._timeline.pop(0) if len(self._timeline) > 1 else self._timeline[0]
        return Info(self.uuid, status, progress=50.0, processing_time=60_000,
                    output=["2026-09-05 15:33:24,155 DEBUG: Matching images"],
                    last_error="boom" if status is TaskStatus.FAILED else "")

    def download_zip(self, dest: str) -> str:
        path = Path(dest) / f"{self.uuid}_all.zip"
        with zipfile.ZipFile(path, "w") as z:
            for name in ASSETS:
                z.writestr(name, b"payload-" + name.encode())
        return str(path)

    def cancel(self) -> None:
        self.cancelled = True


class FakeNode:
    def __init__(self, timeline=(TaskStatus.RUNNING, TaskStatus.COMPLETED)) -> None:
        self.timeline = list(timeline)
        self.created: dict | None = None
        self.task: FakeTask | None = None

    def create_task(self, files, options, name=None, progress_callback=None):
        self.created = {"files": files, "options": options, "name": name}
        if progress_callback:
            progress_callback(40.0)
            progress_callback(100.0)
        self.task = FakeTask("aab831fa-1234", self.timeline)
        return self.task

    def get_task(self, uuid):
        self.task = FakeTask(uuid, self.timeline)
        return self.task


class Sink:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.progress: list[tuple[float, str]] = []

    def on_submitted(self, uuid):
        self.submitted.append(uuid)

    def on_progress(self, pct, stage):
        self.progress.append((pct, stage))


def _job(node, dest, **kw) -> tuple[OdmJob, Sink]:
    sink = Sink()
    job = OdmJob(node, dest, sink=sink, is_cancelled=kw.pop("is_cancelled", lambda: False),
                 sleep_ms=lambda ms: None, **kw)
    return job, sink


def test_fresh_submit_runs_to_completion(tmp_path):
    node = FakeNode()
    cams = tmp_path / "cams.json"
    cams.write_text(json.dumps({"k": 1}))
    job, sink = _job(node, tmp_path / "out", images=[Path("a.jpg"), Path("b.jpg")],
                     name="run1", cameras=cams)

    result = job.run()

    assert node.created["files"] == ["a.jpg", "b.jpg"] and node.created["name"] == "run1"
    assert node.created["options"]["cameras"] == cams.read_text()
    assert node.created["options"]["gltf"] is True and "cameras" not in odm.OPTIONS
    assert sink.submitted == ["aab831fa-1234"]
    assert result.uuid == "aab831fa-1234"
    assert result.glb.read_bytes().startswith(b"payload-") and result.cloud.is_file()
    assert not list((tmp_path / "out").glob("*.zip"))            # zip 은 지운다
    assert node.task.info_calls[1:] == [-1] * (len(node.task.info_calls) - 1)   # 마지막 줄만 받는다
    pcts = [p for p, _ in sink.progress]
    assert pcts[:3] == [0.0, 2.0, 5.0] and pcts[-1] == 100.0 and pcts == sorted(pcts)


def test_resume_skips_submit_and_treats_node_cancel_as_failure(tmp_path):
    node = FakeNode(timeline=[TaskStatus.CANCELED])
    job, sink = _job(node, tmp_path, uuid="deadbeef")
    with pytest.raises(TaskFailedError):
        job.run()
    assert node.created is None and sink.submitted == []


def test_failed_task_raises_with_node_message(tmp_path):
    job, _ = _job(FakeNode(timeline=[TaskStatus.FAILED]), tmp_path, images=[Path("a.jpg")])
    with pytest.raises(TaskFailedError, match="boom"):
        job.run()


def test_cancel_is_noticed_while_polling(tmp_path):
    flag = {"stop": False}
    node = FakeNode(timeline=[TaskStatus.RUNNING, TaskStatus.RUNNING, TaskStatus.COMPLETED])
    job, sink = _job(node, tmp_path, images=[Path("a.jpg")], is_cancelled=lambda: flag["stop"])
    sink.on_progress = lambda pct, stage: flag.__setitem__("stop", pct > 5)   # 첫 폴링 뒤 취소
    with pytest.raises(Cancelled):
        job.run()
    job.cancel_remote()
    assert node.task.cancelled


def test_extract_assets_requires_every_asset(tmp_path):
    zpath = tmp_path / "all.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr(next(iter(ASSETS)), b"x")
    with pytest.raises(OdmError):
        extract_assets(zpath, tmp_path)
    assert not (tmp_path / "model.glb").exists()


def test_stage_text_strips_log_noise_and_truncates():
    info = Info("u", TaskStatus.RUNNING, processing_time=12 * 60_000,
                output=["", "2026-09-05 15:33:24,155 DEBUG: Matching  a very long image name here"])
    text = stage_text(info, expected_min=29, width=20)
    assert text == "Matching a very long · 12분 / 약 29분"
    assert stage_text(Info("u", TaskStatus.QUEUED)) == "대기 중"
