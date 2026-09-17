"""수집본에 YOLO 라벨을 미리 깔아둔다 — 사람이 손으로 다시 그리지 않게.

    uv run python -m tools.autolabel                      # 가장 최근 회차
    uv run python -m tools.autolabel sessions/20260916_184149
    uv run python -m tools.autolabel <폴더> --tile none    # 타일 분할 끄기

**이건 라벨링을 대신해주는 게 아니라 초안을 까는 것이다.** 그대로 학습시키면 지금 모델이
잘못 본 것(노란 안전펜스를 사람으로 보는 것 같은)을 그대로 배운다. 사람이 열어서 고쳐야 한다.

### 타일로 쪼개서 본다 (2026-09-16 실측)

모형이 아주 작다. 1280x960 프레임에서 사람 모형이 **20~30픽셀**이라, 통째로 넣으면
YOLO 가 거의 못 본다. 그래서 화면을 겹치게 쪼개 조각마다 추론하고 좌표를 되돌린다.
조각이 640 으로 확대되면서 모형이 2배 커진다.

    회차 20260916_184149 · 021.jpg 기준
      통째로 (imgsz 1280, conf 0.15)   8개
      타일 4x3   (conf 0.25)          20개   ← 눈으로 확인한 진짜 모형을 대부분 잡았다

겹침(`--overlap`)은 타일 경계에 걸친 모형이 잘려 보이는 것을 막는다. 경계에서 중복으로
잡힌 상자는 NMS 로 합친다.

### 큰 모델은 도움이 안 됐다 (2026-09-16 실측)

`yolo11x`(114MB)를 같은 조건으로 돌려봤다. **사람 18개로 `yolo11n` 과 똑같았고 장당
4초에서 28초로 느려지기만 했다.** 그래서 기본값은 `yolo11n` 그대로 둔다.

### 누워 있는 모형은 자동으로 안 잡힌다

잔해에 깔린 모형은 두 모델 다 거의 놓쳤다. 사전학습 모델이 본 `person` 은 대부분 **서 있는
사람**이라 그렇다. **이건 손으로 그려 넣어야 하고, 그게 이 작업의 핵심이다** —
요구조자 탐지에서 제일 중요한 것이 바로 쓰러진 사람이다.

### 흐린 장은 건너뛴다

흔들린 장은 라벨을 달아도 학습에 도움이 안 되고 사람 시간만 잡아먹는다. 라플라시안
분산이 `--min-blur` 미만이면 라벨을 만들지 않는다 (`.txt` 가 없으면 `make_dataset` 이
가져가지 않는다). 기본 80 은 이 프로젝트가 ODM 수집본에 쓰던 기준과 같다.

### 신뢰도를 너무 내리지 않는다

처음엔 0.03 까지 내렸는데 **잔해 텍스처에 오탐이 수백 개** 쏟아져 라벨로 쓸 수 없었다
(50장에 1694개). 타일로 쪼개면 약한 탐지도 의미가 생기므로 0.25 가 적당하다.

### 너무 큰 상자는 버린다

모형은 화면의 작은 부분이다. 프레임 폭의 `--max-size` 를 넘는 상자는 잔해 덩어리나
건물을 통째로 잡은 것이라 버린다.

### 결과

이미지 옆에 같은 이름의 `.txt` 를 만든다 (YOLO 형식 — labelImg · X-AnyLabeling 이 바로 연다).

    000.jpg  ->  000.txt      `<클래스> <중심x> <중심y> <폭> <높이>`  (전부 0~1 로 정규화)
    classes.txt              클래스 이름. **순서가 곧 번호다 — 바꾸지 않는다**

이미 `.txt` 가 있으면 건드리지 않는다 (`--overwrite` 로 덮어쓴다). 고쳐놓은 것을
다시 돌렸다가 날리는 일이 없어야 한다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import use_utf8_stdout                        # noqa: E402
from src.config import DETECT_MODEL_PATH, SESSIONS_DIR      # noqa: E402
from src.core.detect import COCO_KEEP, is_coco              # noqa: E402
from src.core.imgcheck import list_images                   # noqa: E402

# **순서가 곧 클래스 번호다.** 차량 라벨이 아직 하나도 없어도 자리를 비워둔다 —
# 나중에 차량을 채울 때 사람 라벨을 전부 다시 매기지 않으려면 지금 잡아둬야 한다.
CLASSES = ["person", "vehicle"]

DEFAULT_CONF = 0.25     # 더 내리면 잔해에 오탐이 쏟아진다 (모듈 주석)
TILE_IMGSZ = 640        # 조각 하나를 추론할 크기. 조각이 이만큼 확대된다
DEFAULT_TILE = "4x3"    # 가로x세로 조각 수. "none" 이면 통째로
DEFAULT_OVERLAP = 0.25  # 조각끼리 겹치는 비율
NMS_IOU = 0.5           # 겹친 자리에서 중복으로 잡힌 상자를 합치는 기준
MAX_SIZE = 0.35         # 프레임 폭 대비 이보다 큰 상자는 버린다
MIN_BLUR = 80.0         # 라플라시안 분산이 이 미만이면 건너뛴다 (흔들린 장)


def to_yolo(box, width: int, height: int) -> str:
    """(x1, y1, x2, y2) 픽셀 → `<클래스> <중심x> <중심y> <폭> <높이>` 정규화."""
    name, _, x1, y1, x2, y2 = box
    xc = (x1 + x2) / 2 / width
    yc = (y1 + y2) / 2 / height
    w = (x2 - x1) / width
    h = (y2 - y1) / height
    return f"{CLASSES.index(name)} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}"


def latest_session(sessions_dir: Path = SESSIONS_DIR) -> Path | None:
    """가장 최근 수집 회차. 폴더명이 타임스탬프라 이름순이 곧 시간순이다."""
    if not sessions_dir.is_dir():
        return None
    dirs = sorted(d for d in sessions_dir.iterdir() if d.is_dir())
    return dirs[-1] if dirs else None


def parse_tile(text: str) -> tuple[int, int] | None:
    """"4x3" -> (4, 3). "none" 이면 None (통째로 추론)."""
    if text.lower() in ("none", "off", "0"):
        return None
    cols, _, rows = text.lower().partition("x")
    return int(cols), int(rows)


def tiles(width: int, height: int, grid: tuple[int, int], overlap: float):
    """(x, y, x2, y2) 조각들. 겹치게 잘라 경계에 걸린 모형을 놓치지 않는다."""
    cols, rows = grid
    tw, th = width // cols, height // rows
    step_x, step_y = max(1, int(tw * (1 - overlap))), max(1, int(th * (1 - overlap)))
    for y in range(0, height - 1, step_y):
        for x in range(0, width - 1, step_x):
            yield x, y, min(x + tw, width), min(y + th, height)


def merge(boxes: list, scores: list, iou: float = NMS_IOU) -> list[int]:
    """겹친 조각에서 같은 모형이 두 번 잡힌 것을 합친다. 남길 인덱스를 돌려준다."""
    import torch
    from torchvision.ops import nms

    if not boxes:
        return []
    keep = nms(torch.tensor(boxes, dtype=torch.float32),
               torch.tensor(scores, dtype=torch.float32), iou)
    return keep.tolist()


def detect_image(model, image, coco: bool, conf: float, grid, overlap: float,
                 imgsz: int, max_size: float) -> list:
    """한 장에서 (이름, 신뢰도, x1, y1, x2, y2) 목록. grid 가 None 이면 통째로 본다."""
    height, width = image.shape[:2]
    regions = [(0, 0, width, height)] if grid is None else list(tiles(width, height, grid, overlap))

    boxes, scores, names = [], [], []
    for x, y, x2, y2 in regions:
        crop = image[y:y2, x:x2]
        if crop.size == 0:
            continue
        result = model.predict(crop, conf=conf, imgsz=imgsz, verbose=False)[0]
        for b in result.boxes:
            raw = result.names[int(b.cls)]
            name = COCO_KEEP.get(raw) if coco else raw
            if name not in CLASSES:
                continue
            bx1, by1, bx2, by2 = (float(v) for v in b.xyxy[0].tolist())
            if (bx2 - bx1) > width * max_size or (by2 - by1) > height * max_size:
                continue                    # 잔해 덩어리를 통째로 잡은 상자
            boxes.append([bx1 + x, by1 + y, bx2 + x, by2 + y])
            scores.append(float(b.conf))
            names.append(name)

    return [(names[i], scores[i], *[int(v) for v in boxes[i]]) for i in merge(boxes, scores)]


def label_folder(folder: Path, weights: Path, conf: float, imgsz: int, grid,
                 overlap: float, max_size: float, min_blur: float = 0.0,
                 overwrite: bool = False) -> tuple[int, int, int, int]:
    """폴더를 훑어 라벨을 깐다. (처리한 장, 건너뛴 장, 흐려서 뺀 장, 상자 수)."""
    import cv2
    from ultralytics import YOLO

    from src.core.imgcheck import laplacian_var

    images = list_images(folder)
    if not images:
        raise SystemExit(f"이미지가 없다: {folder}")

    model = YOLO(str(weights))
    coco = is_coco(model.names)
    grid_text = "통째로" if grid is None else f"{grid[0]}x{grid[1]} 타일"
    print(f"  모델 {weights.name} · COCO {coco} · conf {conf} · imgsz {imgsz} · {grid_text}")

    done = skipped = blurry = total = 0
    for path in images:
        out = path.with_suffix(".txt")
        if out.exists() and not overwrite:
            skipped += 1
            continue

        image = cv2.imread(str(path))
        if image is None:
            print(f"  {path.name}: 읽지 못했다")
            continue
        if min_blur > 0:
            sharpness = laplacian_var(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
            if sharpness < min_blur:
                blurry += 1
                print(f"  {path.name}  건너뜀 (흐림 {sharpness:.0f})")
                continue

        height, width = image.shape[:2]
        found = detect_image(model, image, coco, conf, grid, overlap, imgsz, max_size)
        lines = [to_yolo(b, width, height) for b in found]
        out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        done += 1
        total += len(lines)
        print(f"  {path.name}  상자 {len(lines)}")

    (folder / "classes.txt").write_text("\n".join(CLASSES) + "\n", encoding="utf-8")
    return done, skipped, blurry, total


def main(argv: list[str] | None = None) -> None:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description="수집본에 YOLO 라벨 초안을 깐다")
    ap.add_argument("folder", nargs="?", type=Path, help="수집 회차 폴더 (없으면 가장 최근)")
    ap.add_argument("--model", type=Path, default=DETECT_MODEL_PATH,
                    help="사전라벨에 쓸 가중치. 한 번 학습한 뒤에는 그 best.pt 를 주면 "
                         "다음 회차 라벨이 훨씬 정확해진다")
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--imgsz", type=int, default=TILE_IMGSZ, help="조각 하나를 추론할 크기")
    ap.add_argument("--tile", default=DEFAULT_TILE, help='"4x3" 처럼 조각 수. "none" 이면 통째로')
    ap.add_argument("--overlap", type=float, default=DEFAULT_OVERLAP)
    ap.add_argument("--max-size", type=float, default=MAX_SIZE,
                    help="프레임 대비 이보다 큰 상자는 버린다")
    ap.add_argument("--min-blur", type=float, default=MIN_BLUR,
                    help="라플라시안 분산이 이 미만인 흐린 장은 건너뛴다. 0 이면 전부 처리")
    ap.add_argument("--overwrite", action="store_true",
                    help="이미 있는 .txt 도 덮어쓴다 — 손으로 고친 라벨이 날아간다")
    args = ap.parse_args(argv)

    folder = args.folder or latest_session()
    if folder is None:
        raise SystemExit(f"수집 회차가 없다 — 앱의 [드론 상태] 에서 먼저 수집하라 ({SESSIONS_DIR})")
    if not folder.is_dir():
        raise SystemExit(f"폴더가 없다 — {folder}")
    if not args.model.is_file():
        raise SystemExit(f"가중치가 없다 — {args.model}")

    print(f"사전라벨: {folder}")
    done, skipped, blurry, boxes = label_folder(folder, args.model, args.conf, args.imgsz,
                                                parse_tile(args.tile), args.overlap,
                                                args.max_size, args.min_blur, args.overwrite)
    print(f"\n{done}장에 라벨 {boxes}개를 깔았다. 건너뜀 {skipped}장 (이미 .txt 가 있다)")
    print("이제 라벨 도구로 열어 **모형을 직접 고쳐라.** 틀린 상자는 지우고 빠진 것은 그린다.")
    print(f"  classes.txt: {', '.join(CLASSES)}   ← 순서(번호)를 바꾸지 마라")


if __name__ == "__main__":
    main()
