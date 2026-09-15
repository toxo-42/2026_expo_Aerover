"""ODM 투입 전 촬영본 사전 검사.

결과는 print 하지 않고 `CheckReport` 로 돌려준다 — GUI 가 값을 그린다.
446장 검사에 수 분이 걸린다. 그래서 `progress` 콜백과 `cancel` 을 받는다.

    ImageInspector   장 하나 → ImageStat (해상도 · 블러 · 직전 장 매칭 · EXIF)
    RULES            보고서 → 경고·참고. 규칙 하나 = 함수 하나. 추가는 여기 한 줄
    check_folder     폴더 순회 + 진행률 + 취소. 위 둘을 엮기만 한다
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Callable

import cv2
import numpy as np
from PIL import ExifTags, Image

SUFFIXES = {".jpg", ".jpeg", ".png"}

# 판정 임계.
MIN_IMAGES = 40         # 근거리·GPS 없음 조건에서 40~80장 권장
MIN_MATCHES = 100       # 미만이면 중첩이 끊긴 구간

# 매칭 파라미터. 절대값이 아니라 상대 비교가 목적이다.
ORB_FEATURES = 2000
LOWE_RATIO = 0.75
MATCH_SCALE = 0.4       # 매칭은 속도 때문에 축소본으로

# EXIF 태그 이름 → ID 역인덱스
_TAG = {v: k for k, v in ExifTags.TAGS.items()}
_EXIF_IFD = 0x8769      # FocalLength 는 규격상 Exif 서브 IFD 에 있다. 최상위만 보면 놓친다


# ---- 결과 ----

@dataclass
class ImageStat:
    path: Path
    width: int
    height: int
    blur: float                     # 라플라시안 분산. 클수록 선명하다
    matches: int | None             # 직전 장과의 ORB 매칭 수. 첫 장은 None
    model: str | None
    focal: float | None


@dataclass
class CheckReport:
    folder: Path
    stats: list[ImageStat] = field(default_factory=list)
    unreadable: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 차단하지 않는 정보. **재촬영으로 해결되지 않는 것**은 여기 넣는다.
    notes: list[str] = field(default_factory=list)
    cancelled: bool = False         # 중간에 멈췄다 — 판정하지 않았다

    @property
    def count(self) -> int:
        return len(self.stats)

    @property
    def sizes(self) -> set[tuple[int, int]]:
        return {(s.width, s.height) for s in self.stats}

    @property
    def blurs(self) -> list[float]:
        return [s.blur for s in self.stats]

    @property
    def matches(self) -> list[int]:
        return [s.matches for s in self.stats if s.matches is not None]

    @property
    def with_focal(self) -> int:
        return sum(1 for s in self.stats if s.focal)

    @property
    def ok(self) -> bool:
        return not self.cancelled and not self.warnings

    def summary(self) -> str:
        """한 줄 요약. 단계 행의 detail 에 그대로 넣는다."""
        if not self.stats:
            return "이미지가 없다"
        sizes = self.sizes
        if len(sizes) == 1:
            w, h = next(iter(sizes))
            size_txt = f"{w}x{h}"
        else:
            size_txt = "해상도 섞임"
        return f"{self.count}장 · {size_txt} · 블러 중앙 {median(self.blurs):.1f}"


# ---- 장 하나 ----

def laplacian_var(gray: np.ndarray) -> float:
    """선명도 지표. 값이 클수록 선명하다."""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def read_exif(path: Path) -> tuple[str | None, float | None]:
    """(카메라 모델, 초점거리mm) 반환. 없으면 None."""
    try:
        exif = Image.open(path).getexif()
    except Exception:
        return None, None
    model = exif.get(_TAG["Model"])
    focal = exif.get_ifd(_EXIF_IFD).get(_TAG["FocalLength"]) or exif.get(_TAG["FocalLength"])
    return (str(model) if model else None,
            float(focal) if focal else None)


def list_images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in SUFFIXES)


class OverlapMatcher:
    """두 그레이 이미지 간 ORB 매칭 개수. 중첩도가 낮으면 급격히 떨어진다."""

    def __init__(self, features: int = ORB_FEATURES, ratio: float = LOWE_RATIO) -> None:
        self._orb = cv2.ORB_create(features)
        self._bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        self._ratio = ratio

    def count(self, a: np.ndarray, b: np.ndarray) -> int:
        _, da = self._orb.detectAndCompute(a, None)
        _, db = self._orb.detectAndCompute(b, None)
        if da is None or db is None:
            return 0
        pairs = self._bf.knnMatch(da, db, k=2)       # Lowe ratio test
        return sum(1 for p in pairs if len(p) == 2 and p[0].distance < self._ratio * p[1].distance)


class ImageInspector:
    """장 하나를 읽어 ImageStat 을 만든다. 직전 장과 매칭하려고 축소본을 기억한다."""

    def __init__(self, matcher: OverlapMatcher | None = None,
                 match_scale: float = MATCH_SCALE) -> None:
        self._matcher = matcher or OverlapMatcher()
        self._scale = match_scale
        self._prev_small: np.ndarray | None = None

    def inspect(self, path: Path) -> ImageStat | None:
        """읽지 못하면 None."""
        img = cv2.imread(str(path))
        if img is None:
            return None
        h, w = img.shape[:2]

        # 블러 판정은 원본 크기 그대로. 리사이즈하면 수치가 달라진다.
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur = laplacian_var(gray)

        small = cv2.resize(gray, (0, 0), fx=self._scale, fy=self._scale)
        matches = (self._matcher.count(self._prev_small, small)
                   if self._prev_small is not None else None)
        self._prev_small = small

        model, focal = read_exif(path)
        return ImageStat(path, w, h, blur, matches, model, focal)


# ---- 판정 규칙 ----
# 규칙 하나는 보고서를 받아 (경고, 참고) 를 돌려준다. 경고가 하나라도 있으면 ODM 에 넣지 않는다.

Verdict = tuple[list[str], list[str]]
Rule = Callable[[CheckReport], Verdict]


def rule_min_count(r: CheckReport) -> Verdict:
    if r.count < MIN_IMAGES:
        return [f"장수 부족 ({r.count}장). 근거리·GPS 없음 조건에선 {MIN_IMAGES}~80장 권장."], []
    return [], []


def rule_exif(r: CheckReport) -> Verdict:
    """**EXIF 없음은 경고가 아니라 참고다.** 파이 스트림 수집본에는 EXIF 가 원래 없고
    (`core/capture.py` 가 소켓 raw 를 그대로 쓴다) 재촬영해도 생기지 않는다.
    차단하면 드론 회차는 영영 ② 로 못 넘어간다 — cameras.json 으로 푸는 문제다.
    일부에만 섞여 있는 경우는 **그대로 경고**다. 그건 진짜 이상 신호다."""
    n = r.with_focal
    if n == 0:
        return [], ["EXIF 초점거리 없음 — cameras.json 으로 주입한다 (드론 수집본은 정상)."]
    if n < r.count:
        return [f"EXIF 가 일부({n}/{r.count}장)에만 있다. 섞이면 ODM 이 혼란스러워한다."], []
    return [], []


def rule_uniform_size(r: CheckReport) -> Verdict:
    if len(r.sizes) > 1:
        return ["해상도가 섞여 있다. 한 세트로 통일할 것."], []
    return [], []


def rule_readable(r: CheckReport) -> Verdict:
    if r.unreadable:
        return [f"읽지 못한 파일 {len(r.unreadable)}개."], []
    return [], []


def rule_overlap(r: CheckReport) -> Verdict:
    weak = [s.path.name for s in r.stats if s.matches is not None and s.matches < MIN_MATCHES]
    if not weak:
        return [], []
    shown = ", ".join(weak[:5]) + (" …" if len(weak) > 5 else "")
    return [f"매칭 급감 {len(weak)}곳 ({shown}). 중첩 부족 의심."], []


RULES: tuple[Rule, ...] = (rule_min_count, rule_exif, rule_uniform_size, rule_readable, rule_overlap)


def judge(report: CheckReport, rules: tuple[Rule, ...] = RULES) -> Verdict:
    warnings: list[str] = []
    notes: list[str] = []
    for rule in rules:
        w, n = rule(report)
        warnings += w
        notes += n
    return warnings, notes


# ---- 폴더 ----

def check_folder(folder: Path,
                 progress: Callable[[int, int], None] | None = None,
                 cancel: Callable[[], bool] | None = None,
                 inspector: ImageInspector | None = None) -> CheckReport:
    """폴더를 검사해 보고서를 반환한다.

    progress(done, total) 은 장마다 불린다. cancel() 이 True 를 반환하면
    그 시점까지의 부분 보고서를 `cancelled=True` 로 돌려준다 (예외를 던지지 않는다).
    """
    report = CheckReport(folder=folder)
    paths = list_images(folder)
    if not paths:
        report.warnings.append(f"이미지가 없다: {folder}")
        return report

    inspector = inspector or ImageInspector()
    for i, p in enumerate(paths):
        if cancel is not None and cancel():
            report.cancelled = True
            return report
        stat = inspector.inspect(p)
        if stat is None:
            report.unreadable.append(p)
        else:
            report.stats.append(stat)
        if progress:
            progress(i + 1, len(paths))

    report.warnings, report.notes = judge(report)
    return report


def main(argv: list[str]) -> None:
    """단독 실행:  uv run python -m src.core.imgcheck [폴더]"""
    folder = Path(argv[0] if argv else "images")
    rep = check_folder(folder, progress=lambda d, t: print(f"\r{d}/{t}", end="", flush=True))
    print()

    print(f"장수            : {rep.count}")
    print(f"해상도          : {rep.sizes if len(rep.sizes) != 1 else next(iter(rep.sizes))}")
    models = {s.model for s in rep.stats if s.model}
    print(f"EXIF 초점거리   : {rep.with_focal}/{rep.count} 장  모델={models or '없음'}")
    if rep.blurs:
        print(f"블러(라플라시안): min={min(rep.blurs):.1f}  중앙={median(rep.blurs):.1f}  "
              f"max={max(rep.blurs):.1f}")
    if rep.matches:
        print(f"직전 매칭 수    : min={min(rep.matches)}  중앙={median(rep.matches):.0f}  "
              f"max={max(rep.matches)}")
    print("\n[판정]")
    for w in rep.warnings:
        print(f"  ! {w}")
    if rep.ok:
        print("  통과")
    if rep.notes:
        print("\n[참고]")
        for t in rep.notes:
            print(f"  - {t}")


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
