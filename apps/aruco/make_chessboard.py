"""드론 카메라 캘리브레이션용 체스보드 시트 생성 (3차 준비).

배경은 ../ArUco_준비작업.md 7절.
  solvePnP 는 카메라 내부 파라미터를 반드시 요구하는데 저장소에 없다.
  파이 카메라는 EXIF 가 없으므로(cv2.imwrite 경로) 직접 재야 한다.

사양:
  내부 코너  9 x 6      — cv2.findChessboardCorners 에 넣는 값이 이것이다 (사각 수가 아님)
  사각 한 변 20mm       — A4 가로에 보드 둘레 흰 여백 20mm 이상을 남기는 최대치
  용지       A4 가로

사용:
    uv run python make_chessboard.py
    uv run python make_chessboard.py --cols 9 --rows 6 --square 20
"""
import argparse

from PIL import Image, ImageDraw, ImageFont

DPI = 300
MM = DPI / 25.4
A4L = (int(297 * MM), int(210 * MM))   # 가로 3508 x 2480
FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"


def mm(v: float) -> int:
    return int(round(v * MM))


def font(px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, px)


def board_page(cols: int, rows: int, sq_mm: float) -> Image.Image:
    """체스보드 한 장. cols/rows 는 **내부 코너** 수이므로 사각은 +1 씩이다."""
    nx, ny = cols + 1, rows + 1
    w, h = mm(sq_mm * nx), mm(sq_mm * ny)
    quiet = mm(sq_mm)                   # 흰 여백은 사각 한 변 이상
    assert w + 2 * quiet <= A4L[0] and h + 2 * quiet <= A4L[1], \
        f"{nx}x{ny} 사각 x {sq_mm}mm 는 A4 가로에 여백 {sq_mm}mm 를 못 넣는다"

    img = Image.new("RGB", A4L, "white")
    d = ImageDraw.Draw(img)
    x0, y0 = (A4L[0] - w) // 2, (A4L[1] - h) // 2
    s = mm(sq_mm)
    for j in range(ny):
        for i in range(nx):
            if (i + j) % 2 == 0:        # 좌상단이 검정
                d.rectangle([x0 + i * s, y0 + j * s,
                             x0 + (i + 1) * s - 1, y0 + (j + 1) * s - 1], fill="black")

    # 라벨은 보드 x 범위 바깥(좌하단 구석)에 둔다 — 여백을 침범하면 코너 검출에 불리하다
    d.text((mm(8), A4L[1] - mm(11)),
           f"{cols}x{rows} 내부코너 · 사각 {sq_mm:g}mm", font=font(38), fill="black")
    return img


def info_page(cols: int, rows: int, sq_mm: float) -> Image.Image:
    """1쪽 — 인쇄 배율 확인과 촬영 지침.

    A4 가로는 세로 210mm 뿐이라 여백이 빠듯하다. 줄간격·글자크기가 아래 값보다
    커지면 '측정 기록' 이 페이지 밖으로 밀린다.
    """
    img = Image.new("RGB", A4L, "white")
    d = ImageDraw.Draw(img)
    x0, y = mm(20), mm(14)

    d.text((x0, y), "체스보드 — 드론 카메라 캘리브레이션", font=font(70), fill="black")
    y += mm(11)
    d.text((x0, y), f"내부 코너 {cols} x {rows} · 사각 한 변 {sq_mm:g}mm · 다음 쪽에 보드",
           font=font(42), fill="black")

    # 눈금자
    y += mm(14)
    d.text((x0, y), "아래 선이 정확히 100mm 인지 자로 잰다", font=font(46), fill="black")
    y += mm(22)
    d.line([(x0, y), (x0 + mm(100), y)], fill="black", width=6)
    for i in range(11):
        x = x0 + mm(10 * i)
        hh = mm(6) if i % 5 == 0 else mm(3.5)
        d.line([(x, y), (x, y - hh)], fill="black", width=4)
        if i % 5 == 0:
            d.text((x - mm(4), y - hh - mm(6)), f"{10 * i}", font=font(32), fill="black")
    y += mm(7)
    d.text((x0, y), "배율 100%(등배)로 인쇄한다. '페이지에 맞춤'을 끈다.",
           font=font(40), fill="black")

    # 촬영 지침 — 줄 수를 늘리면 아래 '측정 기록' 이 페이지를 넘는다
    y += mm(13)
    d.text((x0, y), "촬영 방법", font=font(52), fill="black")
    y += mm(11)
    for line in [
        "1. 폼보드·유리판에 붙여 완전히 평평하게. 휘면 결과가 통째로 틀어진다. 무광 용지.",
        "2. 드론 카메라로, 실제 스트리밍 경로 그대로 촬영한다.",
        "   [주의] 반드시 1280x720. 해상도가 다르면 값이 안 맞는다.",
        "3. 15~25장. 매번 위치와 기울기를 바꾼다.",
        "   · 중앙뿐 아니라 네 모서리·가장자리에도 보드가 오게 한다.",
        "     왜곡계수는 가장자리에서만 나온다. 중앙만 찍으면 못 구한다.",
        "   · 정면만 찍지 않는다. 기울여야 초점거리와 거리가 분리된다.",
        "   · 보드 전체가 화면 안에 들어와야 한다. 잘리면 그 장은 버려진다.",
        "4. 흔들리지 않게. 블러가 있으면 코너 위치가 밀린다.",
    ]:
        d.text((x0, y), line, font=font(40), fill="black")
        y += mm(7.5)

    # 기록
    y += mm(9)
    d.text((x0, y), "측정 기록", font=font(48), fill="black")
    y += mm(11)
    d.text((x0, y), "인쇄된 사각 한 변 실측 : ______ mm    "
                    "(10칸을 한 번에 재서 10으로 나누면 더 정확하다)",
           font=font(40), fill="black")
    y += mm(9)
    d.text((x0, y), "촬영 해상도 : ______ x ______        장수 : ______",
           font=font(40), fill="black")
    assert y + mm(6) < A4L[1], "1쪽 내용이 A4 가로 높이를 넘는다"
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cols", type=int, default=9, help="가로 내부 코너 수")
    ap.add_argument("--rows", type=int, default=6, help="세로 내부 코너 수")
    ap.add_argument("--square", type=float, default=20.0, help="사각 한 변 (mm)")
    ap.add_argument("--out", default="chessboard.pdf")
    a = ap.parse_args()

    pages = [info_page(a.cols, a.rows, a.square),
             board_page(a.cols, a.rows, a.square)]
    # 1비트 무손실. RGB 로 두면 PIL 이 JPEG 로 넣어 흑백 경계에 링잉이 생긴다.
    pages = [p.convert("1", dither=Image.NONE) for p in pages]
    pages[0].save(a.out, "PDF", resolution=DPI, save_all=True, append_images=pages[1:])
    nx, ny = a.cols + 1, a.rows + 1
    print(f"{a.out} — 2쪽, 내부코너 {a.cols}x{a.rows} (사각 {nx}x{ny}), "
          f"한 변 {a.square:g}mm, 보드 {a.square*nx:g}x{a.square*ny:g}mm")


if __name__ == "__main__":
    main()
