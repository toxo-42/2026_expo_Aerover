"""인쇄용 ArUco 마커 시트 생성 (시연 환경 좌표계 준비).

사양 근거는 ArUco_준비작업.md 2~3절.
  딕셔너리  DICT_4X4_50   — align.py 의 ARUCO_DICT 와 같아야 한다
  개수      8개 (ID 0~7)  — 판 모서리 4 + 변 중앙 4
  한 변     100mm         — 갠트리 궤도 1280x960 에서 먼 쪽 마커도 약 73px
  여백      20mm 이상     — ArUco 는 검은 테두리 바깥 밝은 영역으로 경계를 잡는다

사용:
    uv run python make_markers.py                    # ID 0~7, 100mm
    uv run python make_markers.py --size 80 --ids 6  # 80mm, ID 0~5
    uv run python make_markers.py --start 8 --ids 2  # 추가분만, ID 8~9
"""
import argparse

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

DPI = 300
MM = DPI / 25.4                      # 1mm 당 픽셀
A4 = (int(210 * MM), int(297 * MM))  # 2480 x 3508
QUIET_MM = 20                        # 마커 둘레 흰 여백
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"


def mm(v: float) -> int:
    return int(round(v * MM))


def font(px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, px)


def blank() -> Image.Image:
    return Image.new("RGB", A4, "white")


def marker_img(dict_id: int, marker_id: int, size_mm: float) -> Image.Image:
    """ArUco 마커를 지정 물리 크기의 픽셀 이미지로.

    NEAREST 로 확대한다. 보간을 넣으면 테두리가 흐려져 코너 정밀도가 떨어진다.
    """
    d = cv2.aruco.getPredefinedDictionary(dict_id)
    raw = cv2.aruco.generateImageMarker(d, marker_id, 600)
    px = mm(size_mm)
    return Image.fromarray(raw).convert("RGB").resize((px, px), Image.NEAREST)


def ruler_page(size_mm: float, start: int, n_ids: int) -> Image.Image:
    """1쪽 — 인쇄 배율 확인용 자와 주의사항.

    프린터의 '페이지에 맞춤' 이 크기를 조용히 바꾼다. 그러면 복원 축척이
    통째로 그만큼 틀어지므로, 인쇄 후 반드시 여기서 확인한다.
    """
    img = blank()
    d = ImageDraw.Draw(img)
    x0, y = mm(30), mm(45)

    d.text((x0, mm(22)), "ArUco 마커 시트 — 인쇄 확인", font=font(78), fill="black")
    d.text((x0, mm(34)),
           f"DICT_4X4_50 · ID {start}~{start + n_ids - 1} · 한 변 {size_mm:g}mm",
           font=font(46), fill="black")

    # 100mm 눈금자
    d.text((x0, y), "아래 선이 정확히 100mm 인지 자로 잰다", font=font(50), fill="black")
    y += mm(26)          # 눈금 라벨이 위 문구를 침범하지 않게 띄운다
    d.line([(x0, y), (x0 + mm(100), y)], fill="black", width=6)
    for i in range(11):
        x = x0 + mm(10 * i)
        h = mm(6) if i % 5 == 0 else mm(3.5)
        d.line([(x, y), (x, y - h)], fill="black", width=4)
        if i % 5 == 0:
            d.text((x - mm(4), y - h - mm(6)), f"{10 * i}", font=font(34), fill="black")
    d.text((x0, y + mm(6)),
           "100mm 가 아니면 → 프린터 배율을 100%(등배)로 바꿔 다시 인쇄한다.",
           font=font(42), fill="black")
    d.text((x0, y + mm(13)),
           "그래도 안 맞으면 → 재인쇄하지 말고 실제 잰 값을 그대로 기록해 쓴다.",
           font=font(42), fill="black")

    # 주의사항
    y += mm(30)
    d.text((x0, y), "인쇄·부착 주의", font=font(58), fill="black")
    y += mm(12)
    for line in [
        "· 무광 용지. 광택지는 조명 반사로 검출이 실패한다.",
        "· 마커 둘레 흰 여백을 자르지 않는다 (여백 20mm 포함해 배치했다).",
        "· 폼보드·하드보드에 붙여 평평하게 만든다. 종이가 말리면 코너가 밀린다.",
        "· 인쇄된 마커의 실제 한 변을 재서 기록한다. 이 값이 복원 축척을 정한다.",
        "· 마커 변을 판 변과 나란히 붙인다. 실측 노동이 마커당 3개 값으로 준다.",
        "· 촬영 후 떼지 않는다. 시연 때 solvePnP 가 같은 자리의 같은 마커를 봐야 한다.",
    ]:
        d.text((x0, y), line, font=font(44), fill="black")
        y += mm(9)

    y += mm(8)
    d.text((x0, y), "측정 기록 (여기 적어도 된다)", font=font(50), fill="black")
    y += mm(11)
    d.text((x0, y), "인쇄된 마커 실측 한 변 : ______ mm", font=font(44), fill="black")
    y += mm(10)
    d.text((x0, y), "원점 모서리 사진 파일명 : ____________", font=font(44), fill="black")
    y += mm(12)
    hdr = ["ID", "중심X(m)", "중심Y(m)", "중심Z(m)", "위쪽방향"]
    cols = [x0, x0 + mm(15), x0 + mm(48), x0 + mm(81), x0 + mm(114)]
    for c, h in zip(cols, hdr):
        d.text((c, y), h, font=font(42), fill="black")
    y += mm(9)
    # 줄 간격은 12mm 가 기본이고, ID 가 많으면 A4 안에 들어가게 좁힌다 (8개면 약 9mm)
    row = min(mm(12), (A4[1] - mm(12) - y) // max(n_ids, 1))
    assert row >= mm(8), f"ID {n_ids}개는 1쪽 기록표에 안 들어간다"
    for i in range(start, start + n_ids):
        d.text((cols[0], y), str(i), font=font(42), fill="black")
        for c in cols[1:]:
            d.line([(c, y + mm(7)), (c + mm(28), y + mm(7))], fill="black", width=3)
        y += row
    return img


def marker_pages(dict_id: int, start: int, n_ids: int, size_mm: float) -> list[Image.Image]:
    """마커 2개씩 한 쪽. 둘레 20mm 여백이 확보되도록 배치한다."""
    px = mm(size_mm)
    x = (A4[0] - px) // 2
    quiet = mm(QUIET_MM)
    assert x >= quiet, f"한 변 {size_mm}mm 는 A4 폭에 여백 {QUIET_MM}mm 를 못 넣는다"

    top = mm(25)
    gap = mm(2 * QUIET_MM + 4)
    if top + 2 * px + gap + quiet > A4[1]:      # 두 개가 안 들어가면 한 쪽에 하나
        per_page, ys = 1, [top]
    else:
        per_page, ys = 2, [top, top + px + gap]

    pages = []
    end = start + n_ids
    for first in range(start, end, per_page):
        img = blank()
        d = ImageDraw.Draw(img)
        for k, mid in enumerate(range(first, min(first + per_page, end))):
            y = ys[k]
            img.paste(marker_img(dict_id, mid, size_mm), (x, y))
            # 라벨은 여백 바깥(오른쪽 마진)에 둔다 — 여백을 침범하면 검출에 불리하다
            lx = x + px + quiet + mm(3)
            d.text((lx, y), f"ID {mid}", font=font(64), fill="black")
            d.text((lx, y + mm(9)), f"{size_mm:g}mm", font=font(40), fill="black")
            d.text((lx, y + mm(16)), "4X4_50", font=font(34), fill="black")
        pages.append(img)
    return pages


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=float, default=100.0, help="마커 한 변 (mm)")
    ap.add_argument("--start", type=int, default=0, help="첫 ID")
    ap.add_argument("--ids", type=int, default=8, help="start..start+n-1 개수")
    ap.add_argument("--out", default="aruco_markers.pdf")
    a = ap.parse_args()

    dict_id = cv2.aruco.DICT_4X4_50
    pages = [ruler_page(a.size, a.start, a.ids)] + marker_pages(dict_id, a.start, a.ids, a.size)
    # 1비트로 내려 저장한다. RGB/L 로 두면 PIL 이 PDF 안에 JPEG(DCTDecode)로 넣는데,
    # 흑백 경계에 링잉이 생겨 코너 정밀도가 떨어진다. 여기선 코너가 전부다.
    # dither=NONE — 디더링하면 마커 내부에 없던 점이 생긴다.
    pages = [p.convert("1", dither=Image.NONE) for p in pages]
    pages[0].save(a.out, "PDF", resolution=DPI, save_all=True, append_images=pages[1:])
    print(f"{a.out} — {len(pages)}쪽 (확인 1 + 마커 {a.ids}개), {DPI}dpi, 한 변 {a.size:g}mm")


if __name__ == "__main__":
    main()
