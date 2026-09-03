import os, json, glob, shutil, time
import cv2
import numpy as np

# ===== 경로 설정 =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "received")        # 촬영 원본
DST_DIR = os.path.join(BASE_DIR, "filtered")        # 필터 통과 이미지
OUT_PATH = os.path.join(BASE_DIR, "stitched_map.jpg")  # 최종 결과

# ===== 필터 on/off 스위치 =====
# 텔레메트리 json 이 없으면 알아서 건너뛰므로 켜둬도 무방하지만,
# 실내 테스트처럼 조건이 안 맞을 때는 개별로 끌 수 있게 분리했다.
FILTER_ENABLED = True         # False = 전부 통과 (필터 우회)
CHECK_BLUR = True             # 흔들림/초점
CHECK_ALT = True              # 고도 (json 필요)
CHECK_TILT = True             # roll/pitch (json 필요)
CHECK_GPS = False             # GPS fix/위성 (실내는 무조건 탈락하므로 기본 off)

AUTO_RELAX = True             # 통과 0장이면 블러 기준을 자동 완화해 재시도

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


def imread_safe(path):
    """한글 경로에서도 읽히도록 numpy 버퍼 경유"""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception:
        return None


def scan_frames(paths):
    """모든 프레임의 측정값을 먼저 수집한다 (판정은 나중)"""
    records = []
    for p in paths:
        img = imread_safe(p)
        if img is None:
            records.append({"path": p, "name": os.path.basename(p),
                            "loaded": False})
            continue
        records.append({
            "path": p,
            "name": os.path.basename(p),
            "loaded": True,
            "blur": blur_score(img),
            "meta": load_meta(p),
        })
    return records


def judge(rec, prev_alt, blur_thr):
    """측정값 -> 통과 여부 + 탈락 사유 코드"""
    reasons = []

    if not rec["loaded"]:
        return False, ["로드 실패"], prev_alt

    if CHECK_BLUR and rec["blur"] < blur_thr:
        reasons.append(f"블러 ({rec['blur']:.1f} < {blur_thr:.1f})")

    meta = rec["meta"]
    if meta is None:
        return len(reasons) == 0, reasons, prev_alt

    alt = meta.get("alt_cm")
    roll = meta.get("roll")
    pitch = meta.get("pitch")
    sats = meta.get("sats", 0)
    fix = meta.get("fix", 0)

    if CHECK_ALT and alt is not None:
        lo = TARGET_ALT_CM * (1 - ALT_TOLERANCE)
        hi = TARGET_ALT_CM * (1 + ALT_TOLERANCE)
        if not (lo <= alt <= hi):
            reasons.append(f"고도 이탈 ({alt}cm, 허용 {lo:.0f}~{hi:.0f})")
        if prev_alt is not None and abs(alt - prev_alt) > MAX_ALT_DELTA_CM:
            reasons.append(f"고도 급변 (Δ{abs(alt - prev_alt)}cm)")

    if CHECK_TILT:
        if roll is not None and abs(roll) > MAX_TILT_DEG:
            reasons.append(f"roll 초과 ({roll:.1f}도)")
        if pitch is not None and abs(pitch) > MAX_TILT_DEG:
            reasons.append(f"pitch 초과 ({pitch:.1f}도)")

    if CHECK_GPS:
        if fix < 2:
            reasons.append("GPS fix 없음")
        elif sats < MIN_SATS:
            reasons.append(f"위성 부족 ({sats}개)")

    return len(reasons) == 0, reasons, (alt if alt is not None else prev_alt)


def evaluate(records, blur_thr):
    """전체 판정 -> (통과 레코드, 사유별 집계, 상세 로그)"""
    passed, tally, lines = [], {}, []
    prev_alt = None

    for rec in records:
        ok, reasons, prev_alt = judge(rec, prev_alt, blur_thr)
        if ok:
            passed.append(rec)
            bs = rec.get("blur")
            lines.append(f"[O] {rec['name']}"
                         + (f"  (선명도 {bs:.1f})" if bs is not None else ""))
        else:
            lines.append(f"[X] {rec['name']} - {', '.join(reasons)}")
            for r in reasons:
                key = r.split(" (")[0]
                tally[key] = tally.get(key, 0) + 1

    return passed, tally, lines


def prepare_dst():
    if os.path.isdir(DST_DIR):
        for f in glob.glob(os.path.join(DST_DIR, "*")):
            try:
                os.remove(f)
            except OSError:
                pass
        return True

    for _ in range(5):
        try:
            os.makedirs(DST_DIR, exist_ok=True)
            return True
        except PermissionError:
            time.sleep(0.2)
    print(f"[!] 출력 폴더를 만들지 못했습니다: {DST_DIR}")
    return False


def run_filter():
    print("=" * 50)
    print("[1단계] 프레임 필터링")
    print("=" * 50)

    if not os.path.isdir(SRC_DIR):
        print(f"[!] 원본 폴더가 없습니다: {SRC_DIR}")
        return []

    paths = sorted(glob.glob(os.path.join(SRC_DIR, "*.jpg")), key=natural_key)
    paths += sorted(glob.glob(os.path.join(SRC_DIR, "*.png")), key=natural_key)
    if not paths:
        print(f"[!] {SRC_DIR}에 이미지가 없습니다.")
        return []

    if not prepare_dst():
        return []

    print(f"[*] 원본 {len(paths)}장 발견")

    # --- 측정 ---
    records = scan_frames(paths)

    n_broken = sum(1 for r in records if not r["loaded"])
    n_meta = sum(1 for r in records if r.get("meta") is not None)
    blurs = [r["blur"] for r in records if r["loaded"]]

    print(f"[*] 로드 성공 {len(blurs)}장 / 실패 {n_broken}장")
    print(f"[*] 텔레메트리 메타(json) 보유 {n_meta}/{len(records)}장", end="")
    if n_meta == 0:
        print(" — 고도·기울기·GPS 검사는 자동 생략됩니다")
    else:
        print()

    if blurs:
        arr = np.array(blurs)
        print(f"[*] 선명도(Laplacian var) 최소 {arr.min():.1f} / "
              f"중앙값 {np.median(arr):.1f} / 최대 {arr.max():.1f} "
              f"(기준 {BLUR_THRESHOLD:.1f})")

    active = [n for n, on in [("블러", CHECK_BLUR), ("고도", CHECK_ALT),
                              ("기울기", CHECK_TILT), ("GPS", CHECK_GPS)] if on]
    print(f"[*] 활성 검사: {', '.join(active) if active else '없음'}"
          f"{'' if FILTER_ENABLED else '  (FILTER_ENABLED=False → 전부 통과)'}\n")

    # --- 판정 ---
    if not FILTER_ENABLED:
        passed = [r for r in records if r["loaded"]]
        tally, lines = {}, [f"[O] {r['name']}" for r in passed]
        thr_used = None
    else:
        thr_used = BLUR_THRESHOLD
        passed, tally, lines = evaluate(records, thr_used)

        # 전멸했는데 원인이 블러뿐이라면 기준을 실제 분포에 맞춰 자동 완화
        if not passed and AUTO_RELAX and blurs and set(tally) <= {"블러"}:
            relaxed = max(float(np.percentile(blurs, 25)) * 0.9, 0.5)
            print(f"[!] 통과 0장 — 블러 기준을 {BLUR_THRESHOLD:.1f} → "
                  f"{relaxed:.1f}로 자동 완화해 재판정합니다")
            print(f"    (영구 적용하려면 BLUR_THRESHOLD 를 {relaxed:.1f}로 수정)\n")
            thr_used = relaxed
            passed, tally, lines = evaluate(records, thr_used)

    for ln in lines:
        print(ln)

    # --- 복사 ---
    out_paths = []
    for rec in passed:
        p = rec["path"]
        dst = os.path.join(DST_DIR, os.path.basename(p))
        try:
            shutil.copy(p, dst)
        except OSError as e:
            print(f"[!] 복사 실패 {rec['name']}: {e}")
            continue
        mp = os.path.splitext(p)[0] + ".json"
        if os.path.exists(mp):
            shutil.copy(mp, os.path.join(DST_DIR, os.path.basename(mp)))
        out_paths.append(dst)

    # --- 요약 ---
    print(f"\n총 {len(records)}장 중 {len(out_paths)}장 통과, "
          f"{len(records) - len(out_paths)}장 제외")

    if tally:
        print("탈락 사유:")
        for k, v in sorted(tally.items(), key=lambda x: -x[1]):
            print(f"  - {k}: {v}장")

    if not out_paths:
        print("\n[!] 통과한 프레임이 0장입니다. 아래 순서로 확인하세요:")
        if n_broken == len(records):
            print("    1) 이미지가 전부 로드 실패 — 파일이 깨졌거나 경로에 문제가 있습니다")
        elif "블러" in tally:
            print("    1) 렌즈 초점을 다시 맞추세요 (수동 포커스 C/CS 마운트)")
            print("    2) BLUR_THRESHOLD 를 낮추거나 CHECK_BLUR = False 로 우회")
        if "고도 이탈" in tally:
            print(f"    - TARGET_ALT_CM({TARGET_ALT_CM}) 이 실제 촬영 고도와 다릅니다")
        if "GPS fix 없음" in tally:
            print("    - 실내라면 CHECK_GPS = False 로 두세요")
        print("    * 급할 땐 FILTER_ENABLED = False 로 필터를 통째로 우회할 수 있습니다")
    elif len(out_paths) < len(records) * 0.5:
        print("[!] 통과율이 50% 미만입니다. 촬영 조건 또는 기준값을 재검토하세요.")

    print()
    return out_paths


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
        img = imread_safe(p)
        if img is not None:
            imgs.append((os.path.basename(p), img))

    if len(imgs) < 2:
        return None

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
    imgs = [imread_safe(p) for p in paths]
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