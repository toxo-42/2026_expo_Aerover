"""고친 라벨을 모아 학습용 폴더로 묶는다.

    uv run python -m tools.make_dataset sessions/20260916_190000
    uv run python -m tools.make_dataset sessions/*/ --out dataset --val 0.2

`autolabel` 이 깔고 사람이 고친 `.txt` 들을 ultralytics 가 기대하는 모양으로 복사한다.

    dataset/
    ├── data.yaml              학습이 읽는 설명서 (경로 · 클래스)
    ├── images/train · val
    └── labels/train · val     이미지와 **같은 이름**의 .txt

### val 을 무작위로 나누지 않는다

수집본은 연사다 — 바로 앞뒤 장이 거의 같은 그림이다. 무작위로 나누면 train 에 있던
장면이 val 에도 들어가 **점수만 좋아 보이고 실제로는 못 잡는다.** 그래서 회차 안에서
**뒤쪽 연속 구간**을 val 로 뗀다. 회차가 둘 이상이면 회차마다 그렇게 뗀다.

### 상자가 하나도 없는 장도 넣는다 (일부만)

빈 `.txt` 는 "여기엔 아무것도 없다"는 배경 표본이라 오탐을 줄인다. 다만 너무 많으면
학습이 "아무것도 없다"에 치우친다. 그래서 `--background` 비율(기본 0.1)까지만 넣는다.
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
from src.core.imgcheck import list_images            # noqa: E402

VAL_SHARE = 0.2
BACKGROUND_SHARE = 0.1


def split(pairs: list[tuple[Path, Path]], val_share: float) -> tuple[list, list]:
    """(이미지, 라벨) 목록을 앞=train / 뒤=val 로 자른다 (모듈 주석 — 연사라서)."""
    if len(pairs) < 2:
        return pairs, []
    cut = max(1, int(len(pairs) * (1 - val_share)))
    return pairs[:cut], pairs[cut:]


def collect(folder: Path, background_share: float) -> list[tuple[Path, Path]]:
    """폴더에서 라벨이 있는 (이미지, 라벨) 쌍. 빈 라벨은 비율만큼만 섞는다."""
    labelled, background = [], []
    for image in list_images(folder):
        label = image.with_suffix(".txt")
        if not label.is_file():
            continue                                  # 아직 라벨을 안 깐 장
        (background if not label.read_text(encoding="utf-8").strip() else labelled).append(
            (image, label))

    keep = int(len(labelled) * background_share)
    return labelled + background[:keep]


def copy_pairs(pairs: list[tuple[Path, Path]], out: Path, kind: str, tag: str) -> int:
    """이미지·라벨을 dataset 으로 복사한다. 회차가 섞여도 이름이 겹치지 않게 앞에 태그를 붙인다."""
    img_dir = out / "images" / kind
    lbl_dir = out / "labels" / kind
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    for image, label in pairs:
        stem = f"{tag}_{image.stem}"
        shutil.copy2(image, img_dir / (stem + image.suffix))
        shutil.copy2(label, lbl_dir / (stem + ".txt"))
    return len(pairs)


def write_yaml(out: Path) -> Path:
    """data.yaml. 경로는 **절대경로**다 — Colab 에 올리면 어차피 다시 쓴다."""
    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASSES))
    text = (f"# tools/make_dataset.py 가 만들었다. 클래스 순서는 tools/autolabel.py 의 CLASSES 다.\n"
            f"path: {out.resolve().as_posix()}\n"
            f"train: images/train\n"
            f"val: images/val\n"
            f"names:\n{names}\n")
    path = out / "data.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> None:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description="고친 라벨을 학습용 폴더로 묶는다")
    ap.add_argument("folders", nargs="+", type=Path, help="수집 회차 폴더들")
    ap.add_argument("--out", type=Path, default=ROOT / "dataset")
    ap.add_argument("--val", type=float, default=VAL_SHARE, help="회차마다 뒤에서 뗄 비율")
    ap.add_argument("--background", type=float, default=BACKGROUND_SHARE,
                    help="상자가 없는 장을 라벨된 장 대비 얼마나 섞을지")
    args = ap.parse_args(argv)

    if args.out.exists():
        raise SystemExit(f"이미 있다 — {args.out}\n지우고 다시 하라. 섞이면 train/val 이 오염된다.")

    n_train = n_val = 0
    for folder in args.folders:
        if not folder.is_dir():
            print(f"  건너뜀 (폴더 아님): {folder}")
            continue
        pairs = collect(folder, args.background)
        if not pairs:
            print(f"  건너뜀 (라벨 없음): {folder}  ← tools.autolabel 을 먼저 돌려라")
            continue
        train, val = split(pairs, args.val)
        tag = folder.resolve().name
        n_train += copy_pairs(train, args.out, "train", tag)
        n_val += copy_pairs(val, args.out, "val", tag)
        print(f"  {tag}: train {len(train)} · val {len(val)}")

    if n_train == 0:
        raise SystemExit("학습할 것이 없다. 라벨을 먼저 깔고 고쳐라.")

    yaml_path = write_yaml(args.out)
    print(f"\ntrain {n_train}장 · val {n_val}장 → {args.out}")
    print(f"설명서: {yaml_path}")
    if n_train < 100:
        print("  ※ 100장이 안 된다. 모형을 새로 배우기에는 적다 — 더 모으는 것을 권한다.")


if __name__ == "__main__":
    main()
