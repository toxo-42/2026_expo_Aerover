"""라벨을 이미지에 그려서 저장한다 — 어디를 고쳐야 하는지 눈으로 보려고.

    uv run python -m tools.review sessions/20260916_185141

`review/<회차>/` 에 상자를 그린 사본이 생긴다. 원본과 `.txt` 는 건드리지 않는다.

라벨 도구를 열기 전에 이걸 먼저 넘겨 보면 **무엇이 빠졌는지**가 한눈에 보인다.
특히 잔해에 누운 모형은 자동으로 거의 안 잡히므로, 상자가 없는 자리를 미리 눈에
익혀두고 도구에서 그 자리만 그리면 빠르다.

색: 빨강 = person, 파랑 = vehicle.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import use_utf8_stdout                    # noqa: E402
from tools.autolabel import CLASSES                  # noqa: E402
from src.core.imgcheck import list_images            # noqa: E402

COLORS = [(255, 40, 40), (0, 170, 255)]     # CLASSES 순서와 같다
WIDTH = 3


def draw(image_path: Path, label_path: Path, out_path: Path) -> int:
    from PIL import Image, ImageDraw

    im = Image.open(image_path).convert("RGB")
    w, h = im.size
    d = ImageDraw.Draw(im)
    n = 0
    for line in label_path.read_text(encoding="utf-8").strip().splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        cls = int(parts[0])
        xc, yc, bw, bh = (float(v) for v in parts[1:])
        box = [(xc - bw / 2) * w, (yc - bh / 2) * h, (xc + bw / 2) * w, (yc + bh / 2) * h]
        d.rectangle(box, outline=COLORS[cls % len(COLORS)], width=WIDTH)
        n += 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path, quality=90)
    return n


def main(argv: list[str] | None = None) -> None:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description="라벨을 그려서 review/ 에 저장한다")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    if not args.folder.is_dir():
        raise SystemExit(f"폴더가 없다: {args.folder}")
    out_dir = args.out or (ROOT / "review" / args.folder.resolve().name)

    drawn = total = 0
    for image in list_images(args.folder):
        label = image.with_suffix(".txt")
        if not label.is_file():
            continue                                  # 흐려서 건너뛴 장
        total += draw(image, label, out_dir / image.name)
        drawn += 1

    print(f"{drawn}장에 상자 {total}개를 그렸다 -> {out_dir}")
    print(f"색: {CLASSES[0]}=빨강, {CLASSES[1]}=파랑")


if __name__ == "__main__":
    main()
