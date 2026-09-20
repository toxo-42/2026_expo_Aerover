"""① 입력 검사 결과 캐시.

446장 검사에 약 46초가 든다. 결과를 회차 산출물 폴더에 적어 두면 앱을 껐다
켜도 ② 서브샘플로 바로 이어갈 수 있다 — ② 는 검사가 잰 **블러 값**이 있어야
고를 수 있고, 그 값이 보고서 안에 있다.

캐시가 낡았는지는 입력 폴더의 **파일별 (이름, 크기, 수정시각)** 로 판단한다.
장수만 보면 한 장을 다른 장으로 바꿔치기한 경우를 놓친다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.core.imgcheck import CheckReport, ImageStat, list_images

CACHE_FILE = "check.json"
VERSION = 1


def fingerprint(folder: Path) -> str:
    h = hashlib.sha1()
    for p in list_images(folder):
        st = p.stat()
        h.update(f"{p.name}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def save(report: CheckReport, dest: Path) -> Path | None:
    """취소된 보고서는 판정을 안 한 것이라 저장하지 않는다."""
    if report.cancelled:
        return None
    data = {
        "version": VERSION,
        "folder": str(report.folder),
        "fingerprint": fingerprint(report.folder),
        "warnings": report.warnings,
        "notes": report.notes,
        "unreadable": [str(p) for p in report.unreadable],
        "stats": [
            {"path": str(s.path), "width": s.width, "height": s.height,
             "blur": s.blur, "matches": s.matches, "model": s.model, "focal": s.focal}
            for s in report.stats
        ],
    }
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / CACHE_FILE
    out.write_text(json.dumps(data))
    return out


def load(folder: Path, dest: Path) -> CheckReport | None:
    """입력 폴더가 저장 당시와 같을 때만 보고서를 돌려준다. 아니면 None."""
    path = dest / CACHE_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if data.get("version") != VERSION or data.get("fingerprint") != fingerprint(folder):
        return None
    return CheckReport(
        folder=folder,
        stats=[ImageStat(path=Path(s["path"]), width=s["width"], height=s["height"],
                         blur=s["blur"], matches=s["matches"], model=s["model"],
                         focal=s["focal"]) for s in data["stats"]],
        unreadable=[Path(p) for p in data["unreadable"]],
        warnings=data["warnings"],
        notes=data["notes"],
    )
