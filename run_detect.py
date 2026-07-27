# run_detect.py — 객체 탐지 모듈
#
# 이 파일은 대시보드와 독립적으로 동작한다.
# 대시보드는 `import run_detect as det` 로 이 파일을 불러 detect() 를 호출한다.
#
# ┌─────────────────────────────────────────────────────────────┐
# │  YOLO 담당자가 채워야 할 곳은 detect() 함수 하나뿐입니다.      │
# │  나머지(박스 그리기·집계·스티칭 연동)는 대시보드가 처리합니다. │
# └─────────────────────────────────────────────────────────────┘

import os
import glob
import cv2


# ===== 설정 =====
MODEL_PATH = "best.pt"          # 팀원의 학습된 모델 파일 (없으면 yolo11n.pt)
CONF_THRESHOLD = 0.35           # 이 신뢰도 미만은 버림

# 클래스별 박스 색 (BGR). 대시보드 집계도 이 이름을 기준으로 센다.
DET_COLORS = {
    "person": (0, 80, 255),     # 빨강 — 사람
    "car": (0, 200, 255),       # 노랑 — 차량
    "truck": (0, 200, 255),
    "bus": (0, 200, 255),
}

# 사람/차량으로 집계할 클래스 이름
PERSON_CLASSES = {"person"}
VEHICLE_CLASSES = {"car", "truck", "bus"}


# 모델을 한 번만 로드해서 재사용 (매 호출마다 로드하면 느리다)
_model = None


def _load_model():
    """YOLO 모델을 지연 로드한다. 팀원 코드에서 사용."""
    global _model
    if _model is not None:
        return _model
    # ── 팀원 YOLO 로드 코드 ──────────────────────────────
    # from ultralytics import YOLO
    # path = MODEL_PATH if os.path.exists(MODEL_PATH) else "yolo11n.pt"
    # _model = YOLO(path)
    # return _model
    # ─────────────────────────────────────────────────────
    return None


# ==========================================================
#  ★ 팀원이 채우는 함수 — 이것 하나만 구현하면 된다 ★
# ==========================================================

def detect(image_path):
    """이미지 한 장에서 객체를 검출한다.

    입력:  image_path (str) — 이미지 파일 경로
    반환:  [(class_name, confidence, x1, y1, x2, y2), ...]
           - class_name: "person" / "car" 등 문자열
           - confidence: 0.0 ~ 1.0
           - x1,y1,x2,y2: 박스 좌상단·우하단 픽셀 좌표 (int)
           검출이 없으면 빈 리스트 []

    현재는 미구현 상태라 빈 리스트를 반환한다.
    아래 예시 주석을 참고해 YOLO 모델을 연결하면 된다.
    """
    # ── 구현 예시 (ultralytics YOLOv11) ──────────────────
    # model = _load_model()
    # if model is None:
    #     return []
    # result = model(image_path, conf=CONF_THRESHOLD, verbose=False)[0]
    # out = []
    # for cls_id, conf, xyxy in zip(result.boxes.cls,
    #                               result.boxes.conf,
    #                               result.boxes.xyxy.tolist()):
    #     name = model.names[int(cls_id)]
    #     x1, y1, x2, y2 = map(int, xyxy)
    #     out.append((name, float(conf), x1, y1, x2, y2))
    # return out
    #
    # ── SAHI(타일 분할)를 쓰는 경우 ──────────────────────
    # 작은 객체(항공 사진 속 사람) 검출률을 높이려면 SAHI 사용.
    # from sahi import AutoDetectionModel
    # from sahi.predict import get_sliced_prediction
    # ...
    # ─────────────────────────────────────────────────────
    return []


# ==========================================================
#  이 아래는 대시보드가 쓰는 유틸 — 수정할 필요 없음
# ==========================================================

def draw_boxes(img, dets):
    """검출 박스를 이미지에 그려서 돌려준다."""
    for cls, conf, x1, y1, x2, y2 in dets:
        color = DET_COLORS.get(cls, (0, 255, 120))
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 3)
        label = f"{cls} {conf:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.rectangle(img, (x1, y1 - th - 8), (x1 + tw + 6, y1), color, -1)
        cv2.putText(img, label, (x1 + 3, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
    return img


def count_classes(dets):
    """검출 결과에서 사람·차량 수를 센다. 반환: (사람 수, 차량 수)"""
    p = sum(1 for d in dets if d[0] in PERSON_CLASSES)
    c = sum(1 for d in dets if d[0] in VEHICLE_CLASSES)
    return p, c


def detect_folder(src_dir):
    """폴더 안 모든 jpg에 detect()를 돌린다.
    반환: {파일명: 검출리스트} 딕셔너리 — 단독 테스트용."""
    results = {}
    for p in sorted(glob.glob(os.path.join(src_dir, "*.jpg"))):
        results[os.path.basename(p)] = detect(p)
    return results


# ==========================================================
#  단독 실행 — 팀원이 자기 모델을 테스트할 때
#  사용법:  python run_detect.py received
# ==========================================================

def main():
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "received"
    print("=" * 50)
    print("객체 탐지 단독 테스트")
    print("=" * 50)

    if not os.path.isdir(src):
        print(f"[!] 폴더가 없습니다: {src}")
        return

    results = detect_folder(src)
    if not results:
        print(f"[!] {src} 에 이미지가 없습니다.")
        return

    total_p = total_c = 0
    for fname, dets in results.items():
        p, c = count_classes(dets)
        total_p += p
        total_c += c
        if dets:
            print(f"[O] {fname} — 사람 {p}, 차량 {c} (총 {len(dets)}개 검출)")
        else:
            print(f"[ ] {fname} — 검출 없음")

    print("-" * 50)
    print(f"전체: 사람 {total_p}명, 차량 {total_c}대")

    if total_p + total_c == 0:
        print()
        print("[!] 검출이 0건입니다.")
        print("    detect() 함수가 아직 비어 있거나 모델이 연결되지 않았습니다.")


if __name__ == "__main__":
    main()