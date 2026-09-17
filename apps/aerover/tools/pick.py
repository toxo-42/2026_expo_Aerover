"""라벨할 장을 골라 작업 폴더로 뽑는다 — 한 바퀴에 조금씩 손보기 위해.

    uv run python -m tools.pick sessions/20260916_185141 --count 10

50장을 한 번에 손보는 것은 지치고, 앞뒤 장이 비슷해 노동 대비 얻는 것도 적다.
그래서 **묶음마다 가장 선명한 한 장**만 뽑는다 — 선명하면서 장면도 겹치지 않는다.
고르는 방법은 `core/subsample.select` 를 그대로 쓴다 (연사본 선별에 쓰던 것과 같은 규칙).

뽑은 장은 이미지와 `.txt` 를 **복사**한다. 원본 회차는 손대지 않는다 — 여기서 고친
라벨이 원본을 덮지 않아야 다음 바퀴에 원본을 다시 쓸 수 있다.

    label/<회차>_r1/   ← 여기서 손으로 고친다. 그대로 make_dataset 에 넣는다
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import use_utf8_stdout                    # noqa: E402
from tools.autolabel import CLASSES                  # noqa: E402
from src.core.imgcheck import ImageStat, list_images  # noqa: E402
from src.core.subsample import select                 # noqa: E402

DEFAULT_COUNT = 10


def sharpness(path: Path) -> float:
    import cv2
    from src.core.imgcheck import laplacian_var
    image = cv2.imread(str(path))
    if image is None:
        return 0.0
    return laplacian_var(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))


def stats_for(paths: list[Path]) -> list[ImageStat]:
    """`subsample.select` 가 보는 것은 `.blur` 와 `.path` 뿐이다. 나머지는 채우지 않는다."""
    return [ImageStat(path=p, width=0, height=0, blur=sharpness(p),
                      matches=None, model=None, focal=None) for p in paths]


def main(argv: list[str] | None = None) -> None:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description="라벨할 장을 골라 작업 폴더로 복사한다")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--count", type=int, default=DEFAULT_COUNT)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    # 라벨이 있는 장만 후보다 — 없는 장은 autolabel 이 흐리다고 뺀 것이다.
    candidates = [p for p in list_images(args.folder) if p.with_suffix(".txt").is_file()]
    if not candidates:
        raise SystemExit(f"라벨이 있는 장이 없다: {args.folder}  (tools.autolabel 을 먼저 돌려라)")

    group = max(1, len(candidates) // args.count)
    picked = select(stats_for(candidates), group)[:args.count]

    out = args.out or (ROOT / "label" / f"{args.folder.resolve().name}_r1")
    out.mkdir(parents=True, exist_ok=True)
    for s in picked:
        shutil.copy2(s.path, out / s.path.name)
        shutil.copy2(s.path.with_suffix(".txt"), out / (s.path.stem + ".txt"))
    (out / "classes.txt").write_text("\n".join(CLASSES) + "\n", encoding="utf-8")

    print(f"후보 {len(candidates)}장에서 {len(picked)}장을 뽑았다 (묶음 {group}장마다 가장 선명한 한 장)")
    for s in picked:
        print(f"  {s.path.name}  선명도 {s.blur:.0f}")
    print(f"\n작업 폴더: {out}")


if __name__ == "__main__":
    main()
