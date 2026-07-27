import os, json, glob, shutil, time
import cv2
import numpy as np

# ===== 경로 설정 =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "received")        # 촬영 원본
DST_DIR = os.path.join(BASE_DIR, "filtered")        # 필터 통과 이미지
OUT_PATH = os.path.join(BASE_DIR, "stitched_map.jpg")  # 최종 결과

# ===== 필터 파라미터 =====
TARGET_ALT_CM = 1500          # 목표 고도 (cm) — 데모 조건에 맞게 수정
ALT_TOLERANCE = 0.10          # ±10%
MAX_TILT_DEG = 8.0            # roll/pitch 허용 최대 기울기
MIN_SATS = 6
BLUR_THRESHOLD = 10.0         # 스트림 JPEG 기준 하향 조정
MAX_ALT_DELTA_CM = 50         # 3초 간격 기준 인접 프레임 고도 변화 허용치

# ===== 스티칭 파라미터 =====
STITCH_MAX_DIM = 1600         # 정합용 최대 변 길이 (px)
PANO_CONF_THRESH = 0.6        # 낮출수록 관대 (기본 1.0)
MIN_MATCH_COUNT = 20
ORB_FEATURES = 3000
CANVAS_SCALE = 4.0            # 결과 캔버스를 원본 대비 몇 배로
ARUCO_DICT = cv2.aruco.DICT_4X4_50


# ========== 필터링 ==========

def load_meta(img_path):
    meta_path = os.path.splitext(img_path)[0] + ".json"
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def blur_score(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var()


def natural_key(path):
    """001.jpg, 002.jpg ... 형식을 숫자 순으로 정렬"""
    base = os.path.splitext(os.path.basename(path))[0]
    digits = "".join(ch for ch in base if ch.isdigit())
    return (int(digits) if digits else 0, base)


def check_frame(img, meta, prev_alt):
    reasons = []

    bs = blur_score(img)
    if bs < BLUR_THRESHOLD:
        reasons.append(f"블러 (score={bs:.1f})")

    if meta is None:
        return len(reasons) == 0, reasons, None

    alt = meta.get("alt_cm")
    roll = meta.get("roll")
    pitch = meta.get("pitch")
    sats = meta.get("sats", 0)
    fix = meta.get("fix", 0)

    if alt is not None:
        lo = TARGET_ALT_CM * (1 - ALT_TOLERANCE)
        hi = TARGET_ALT_CM * (1 + ALT_TOLERANCE)
        if not (lo <= alt <= hi):
            reasons.append(f"고도 이탈 ({alt}cm)")
        if prev_alt is not None and abs(alt - prev_alt) > MAX_ALT_DELTA_CM:
            reasons.append(f"고도 급변 (Δ{abs(alt - prev_alt)}cm)")

    if roll is not None and abs(roll) > MAX_TILT_DEG:
        reasons.append(f"roll 초과 ({roll:.1f}도)")
    if pitch is not None and abs(pitch) > MAX_TILT_DEG:
        reasons.append(f"pitch 초과 ({pitch:.1f}도)")

    if fix < 2:
        reasons.append("GPS fix 없음")
    elif sats < MIN_SATS:
        reasons.append(f"위성 부족 ({sats}개)")

    return len(reasons) == 0, reasons, alt


def run_filter():
    print("=" * 50)
    print("[1단계] 프레임 필터링")
    print("=" * 50)

    if not os.path.isdir(SRC_DIR):
        print(f"[!] 원본 폴더가 없습니다: {SRC_DIR}")
        return []

    # 재실행 대비 초기화 (Windows는 삭제가 비동기라 재생성이 실패할 수 있음)
    if os.path.isdir(DST_DIR):
        for f in glob.glob(os.path.join(DST_DIR, "*")):
            try:
                os.remove(f)
            except OSError:
                pass
    else:
        for _ in range(5):
            try:
                os.makedirs(DST_DIR, exist_ok=True)
                break
            except PermissionError:
                time.sleep(0.2)

    paths = sorted(glob.glob(os.path.join(SRC_DIR, "*.jpg")), key=natural_key)
    if not paths:
        print(f"[!] {SRC_DIR}에 이미지가 없습니다.")
        return []

    print(f"[*] 원본 {len(paths)}장 발견\n")

    prev_alt = None
    passed = []

    for p in paths:
        img = cv2.imread(p)
        if img is None:
            print(f"[X] {os.path.basename(p)} - 로드 실패")
            continue

        meta = load_meta(p)
        ok, reasons, alt = check_frame(img, meta, prev_alt)

        if ok:
            dst = os.path.join(DST_DIR, os.path.basename(p))
            shutil.copy(p, dst)
            mp = os.path.splitext(p)[0] + ".json"
            if os.path.exists(mp):
                shutil.copy(mp, os.path.join(DST_DIR, os.path.basename(mp)))
            passed.append(dst)
            print(f"[O] {os.path.basename(p)}")
        else:
            print(f"[X] {os.path.basename(p)} - {', '.join(reasons)}")

        if alt is not None:
            prev_alt = alt

    print(f"\n총 {len(paths)}장 중 {len(passed)}장 통과, {len(paths) - len(passed)}장 제외")

    if len(passed) < len(paths) * 0.5:
        print("[!] 통과율이 낮습니다. BLUR_THRESHOLD 값을 낮춰보세요.")

    print()
    return passed


# ========== 스티칭 ==========

def compute_homography(img1, img2, orb, matcher):
    """img2 -> img1 방향 homography"""
    g1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)

    kp1, des1 = orb.detectAndCompute(g1, None)
    kp2, des2 = orb.detectAndCompute(g2, None)

    if des1 is None or des2 is None:
        return None, 0

    matches = matcher.knnMatch(des1, des2, k=2)

    good = []
    for m_n in matches:
        if len(m_n) != 2:
            continue
        m, n = m_n
        if m.distance < 0.75 * n.distance:
            good.append(m)

    if len(good) < MIN_MATCH_COUNT:
        return None, len(good)

    src_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)

    H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    inliers = int(mask.sum()) if mask is not None else 0

    return H, inliers


def warp_onto_canvas(canvas, img, H):
    h, w = canvas.shape[:2]
    warped = cv2.warpPerspective(img, H, (w, h))
    mask = (warped.sum(axis=2) > 0)
    canvas[mask] = warped[mask]
    return canvas


def detect_aruco(img):
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    params = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(aruco_dict, params)
    corners, ids, _ = detector.detectMarkers(img)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2) for c, i in zip(corners, ids.flatten())}


def stitch_sequential(paths):
    """순차 homography 방식 — Stitcher 실패 시 폴백"""
    print("[*] 순차 정합 방식으로 전환합니다")

    imgs = []
    for p in paths:
        img = cv2.imread(p)
        if img is not None:
            imgs.append((os.path.basename(p), img))

    h, w = imgs[0][1].shape[:2]
    canvas_w = int(w * CANVAS_SCALE)
    canvas_h = int(h * CANVAS_SCALE)
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)

    offset_x = (canvas_w - w) // 2
    offset_y = (canvas_h - h) // 2
    T = np.array([[1, 0, offset_x],
                  [0, 1, offset_y],
                  [0, 0, 1]], dtype=np.float64)

    canvas = warp_onto_canvas(canvas, imgs[0][1], T)
    print(f"[O] {imgs[0][0]} - 기준 프레임")

    orb = cv2.ORB_create(nfeatures=ORB_FEATURES)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)

    H_accum = T.copy()
    prev_img = imgs[0][1]
    stitched = 1
    failed = 0

    for name, img in imgs[1:]:
        H, inliers = compute_homography(prev_img, img, orb, matcher)

        if H is None:
            print(f"[X] {name} - 정합 실패 (매칭 {inliers}개)")
            failed += 1
            continue

        H_accum = H_accum @ H
        canvas = warp_onto_canvas(canvas, img, H_accum)
        prev_img = img
        stitched += 1
        print(f"[O] {name} - inlier {inliers}개")

    # 여백 잘라내기
    gray = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
    coords = cv2.findNonZero(gray)
    if coords is not None:
        x, y, cw, ch = cv2.boundingRect(coords)
        canvas = canvas[y:y+ch, x:x+cw]

    print(f"  {stitched}/{len(imgs)}장 정합")
    if failed:
        print(f"  {failed}장 실패 — 겹침 부족 또는 특징점 부족")

    return canvas


# ---------- OpenCV Stitcher (기본 경로) ----------

def stitch_opencv(paths):
    """bundle adjustment + multi-band blending 을 포함한 정식 스티칭"""
    imgs = [cv2.imread(p) for p in paths]
    imgs = [im for im in imgs if im is not None]
    if len(imgs) < 2:
        return None

    # 큰 이미지는 축소해서 정합 (속도 + 안정성)
    scaled = []
    for im in imgs:
        h, w = im.shape[:2]
        if max(h, w) > STITCH_MAX_DIM:
            r = STITCH_MAX_DIM / max(h, w)
            im = cv2.resize(im, (int(w * r), int(h * r)),
                            interpolation=cv2.INTER_AREA)
        scaled.append(im)

    stitcher = cv2.Stitcher_create(cv2.Stitcher_SCANS)
    stitcher.setPanoConfidenceThresh(PANO_CONF_THRESH)

    status, pano = stitcher.stitch(scaled)

    if status == cv2.Stitcher_OK:
        print(f"  {len(scaled)}장 전역 최적화 완료")
        return pano

    reason = {
        cv2.Stitcher_ERR_NEED_MORE_IMGS:
            "겹치는 영역이 부족합니다 (오버랩 60~70% 필요)",
        cv2.Stitcher_ERR_HOMOGRAPHY_EST_FAIL:
            "변환 추정 실패 — 특징점이 부족하거나 장면이 평면적이지 않습니다",
        cv2.Stitcher_ERR_CAMERA_PARAMS_ADJUST_FAIL:
            "카메라 파라미터 보정 실패 — 촬영 각도 변화가 큽니다",
    }.get(status, f"알 수 없는 오류 (status={status})")
    print(f"[!] 전역 정합 실패: {reason}")
    return None


def crop_black(img):
    """가장자리 검은 여백 제거"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 1, 255, cv2.THRESH_BINARY)
    coords = cv2.findNonZero(mask)
    if coords is None:
        return img
    x, y, w, h = cv2.boundingRect(coords)
    return img[y:y + h, x:x + w]


def run_stitch(paths):
    print("=" * 50)
    print("[2단계] 이미지 스티칭")
    print("=" * 50)

    if len(paths) < 2:
        print("[!] 정합하려면 통과 이미지가 2장 이상 필요합니다.")
        return

    if os.path.exists(OUT_PATH):
        os.remove(OUT_PATH)

    print(f"[*] 입력 {len(paths)}장")

    # 1순위: OpenCV Stitcher (전역 최적화 + 블렌딩)
    result = stitch_opencv(paths)
    method = "전역 최적화"

    # 2순위: 순차 homography
    if result is None:
        result = stitch_sequential(paths)
        method = "순차 정합"

    if result is None or result.size == 0:
        print("[X] 스티칭에 실패했습니다.")
        return

    result = crop_black(result)
    cv2.imwrite(OUT_PATH, result)

    print(f"\n[O] {method} → {OUT_PATH}")
    print(f"    결과 크기: {result.shape[1]} x {result.shape[0]}")

    markers = detect_aruco(result)
    if markers:
        print(f"[*] ArUco 마커 {len(markers)}개 검출: ID {sorted(markers.keys())}")
        for mid, corners in markers.items():
            cx, cy = corners.mean(axis=0)
            print(f"    ID {mid} 중심 픽셀: ({cx:.1f}, {cy:.1f})")
    else:
        print("[*] ArUco 마커 미검출 — 픽셀 좌표계로만 유지")


# ========== 실행 ==========

def main():
    passed = run_filter()
    run_stitch(passed)
    print("\n완료.")


if __name__ == "__main__":
    main()