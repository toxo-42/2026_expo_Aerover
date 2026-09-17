"""yolo11n 에서 파인튜닝 — 모형을 배우게 한다.

    uv run python -m tools.train                       # dataset/ 을 학습
    uv run python -m tools.train --epochs 100 --device 0

Colab 에서도 **같은 파일**이 돈다 (README "모형 학습" 절). 노트북을 따로 두지 않는 것은
셀 안에 복사된 코드가 이 파일과 어긋나기 시작하면 어느 쪽이 맞는지 알 수 없어서다.

### 처음부터(scratch) 배우지 않는다

수백 장으로는 사물 인식을 새로 배울 수 없다. 이미 사람을 아는 `yolo11n.pt` 에서
출발해 **모형도 사람으로 보도록 옮기는 것**이 목표다.

### 진짜 사람이 섞여 있어야 한다

모형만 학습시키면 원래 알던 사람을 잊는다 (catastrophic forgetting). 수집본에
진짜 사람이 찍힌 장이 섞여 있어야 둘 다 잡는다 — 그건 데이터를 모을 때 정할 일이고,
여기서는 시작 시점에 한 번 경고만 한다.

### 증강을 올린다 — 배경을 외우지 못하게

**장면이 하나뿐인 것이 이 데이터의 가장 큰 약점이다.** 같은 디오라마·같은 조명으로 열 몇 장을
배우면, 모델이 "사람의 생김새" 대신 **"이 벽돌벽 · 이 회색 잔해 질감 · 이 조명"** 을 단서로
삼아버린다. 현장에서 잔해를 다시 쌓거나 조명이 달라지면 그대로 무너진다.

그래서 ultralytics 기본값보다 세게 흔든다.

    flipud 0.5     위아래 뒤집기 — 내려다보는 시점이라 위아래가 뒤집혀도 같은 것이다
    degrees 30     회전 — 쓰러진 모형은 어느 방향으로도 누울 수 있다
    scale 0.6      크기 — 촬영 고도가 달라지는 것을 흉내낸다
    translate 0.2  위치 — 모형이 화면 어디에 있든 같게 보도록
    hsv_v 0.5      밝기 — 조명이 달라지는 것을 흉내낸다

회전을 180도까지 주지 않는 이유는 **순수한 수직 촬영이 아니라 비스듬히 내려다본 구도**라,
건물이 거꾸로 서는 그림은 실제로 나오지 않기 때문이다.

증강으로 메울 수 있는 것에는 한계가 있다 — **배치를 바꿔 다시 찍는 것이 늘 더 낫다.**
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

BASE = "yolo11n.pt"     # 출발점. 더 정확하게 하려면 yolo11s.pt (느려진다)
EPOCHS = 300
IMGSZ = 1280            # 수집본 원본 크기. 줄이면 20~30픽셀짜리 모형이 뭉개진다
PATIENCE = 100          # val 이 몇 장뿐이라 점수가 출렁인다. 성급히 멈추지 않는다

# 증강 (모듈 주석 — 장면이 하나뿐인 것을 메우려는 것이다)
AUGMENT = {"flipud": 0.5, "degrees": 30.0, "scale": 0.6, "translate": 0.2, "hsv_v": 0.5}


def train(data: Path, base: str, epochs: int, imgsz: int, device: str | None,
          project: Path, name: str, degrees: float | None = None):
    from ultralytics import YOLO

    augment = dict(AUGMENT)
    if degrees is not None:
        augment["degrees"] = degrees        # 회전이 작은 모형에 해로운지 비교할 때 쓴다
    model = YOLO(base)
    return model.train(data=str(data), epochs=epochs, imgsz=imgsz, device=device,
                       patience=PATIENCE, project=str(project), name=name,
                       exist_ok=True, **augment)


def main(argv: list[str] | None = None) -> None:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description="수집본으로 yolo11n 을 파인튜닝한다")
    ap.add_argument("--data", type=Path, default=ROOT / "dataset" / "data.yaml")
    ap.add_argument("--base", default=BASE, help="출발 가중치")
    ap.add_argument("--epochs", type=int, default=EPOCHS)
    ap.add_argument("--imgsz", type=int, default=IMGSZ)
    ap.add_argument("--device", default=None,
                    help="'0' 이면 첫 GPU, 'cpu' 면 CPU. 비우면 알아서 고른다")
    ap.add_argument("--project", type=Path, default=ROOT / "runs")
    ap.add_argument("--name", default="train")
    ap.add_argument("--degrees", type=float, default=None,
                    help="회전 증강 각도. 0 이면 끈다 (기본은 AUGMENT 의 값)")
    ap.add_argument("--install", action="store_true",
                    help="끝나면 models/best.pt 로 복사한다 — 앱이 바로 쓴다")
    args = ap.parse_args(argv)

    if not args.data.is_file():
        raise SystemExit(f"설명서가 없다 — {args.data}\ntools.make_dataset 를 먼저 돌려라.")

    print(f"학습: {args.base} → {args.data}")
    print("  ※ 진짜 사람이 찍힌 장이 섞여 있는지 확인하라. 모형만 배우면 사람을 잊는다.")
    result = train(args.data, args.base, args.epochs, args.imgsz, args.device,
                   args.project, args.name, args.degrees)

    best = Path(result.save_dir) / "weights" / "best.pt"
    print(f"\n끝났다: {best}")
    if args.install:
        dst = ROOT / "models" / "best.pt"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, dst)
        print(f"설치: {dst}  ← 앱을 다시 켜면 이걸 쓴다")
    else:
        print(f"앱에 넣으려면:  copy \"{best}\" \"{ROOT / 'models' / 'best.pt'}\"")


if __name__ == "__main__":
    main()
