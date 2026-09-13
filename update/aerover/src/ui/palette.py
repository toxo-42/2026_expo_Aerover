"""색상 팔레트 — 콘티 6절. QSS 와 GL 셰이더가 같은 값을 쓴다."""

BG_MAIN = "#F8F9FA"        # 앱 배경 (아주 옅은 쿨그레이)
BG_PANEL = "#FFFFFF"       # 패널 배경 (플로팅 HUD 는 여기에 투명도를 얹는다)
BORDER_LINE = "#E2E8F0"    # 경계선
TEXT_PRIMARY = "#0A2540"   # 본문 (깊은 네이비)
TEXT_DIM = "#8296AB"       # 보조 정보

ACCENT_BLUE = "#0056D8"    # 기능색 — 활성 토글·슬라이더·버튼·선택 윤곽
WARN_AMBER = "#F59E0B"     # 주의 — 좌표계 정합 배너·배터리 경고·링크 약함
STATUS_RED = "#EF4444"     # 위험 — 탐지 박스·모델 없음·오류

# 정상은 시안인데, **흰 패널 위에서는 대비가 1.7:1 이라 글자로 못 쓴다.**
# 그래서 어두운 면(뷰포트·HUD) 위에는 STATUS_OK, 흰 패널 위 글자에는
# 같은 색조를 어둡게 내린 STATUS_OK_TEXT(5.4:1) 를 쓴다.
STATUS_OK = "#38DCF0"
STATUS_OK_TEXT = "#0E7490"

# 3D 뷰포트·영상 바탕. 모델과 영상을 돋보이게 하는 다크 슬레이트.
VIEWPORT_BG = "#131922"


def rgbf(hex_color: str) -> tuple[float, float, float]:
    """'#RRGGBB' → (r, g, b) 0.0~1.0. glClearColor 용."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def app_qss() -> str:
    """앱 전역 스타일. Streamlit 때와 달리 프레임워크 내부 선택자가 아니라
    우리 위젯의 objectName·클래스에만 건다."""
    return f"""
    QWidget {{
        background: {BG_MAIN};
        color: {TEXT_PRIMARY};
        font-family: 'Helvetica Neue', 'Apple SD Gothic Neo';
        font-size: 13px;
    }}
    QFrame#panel {{
        background: {BG_PANEL};
        border: 1px solid {BORDER_LINE};
        border-radius: 8px;
    }}
    /* 라벨은 배경을 칠하지 않는다 — 흰 패널 위에서 얼룩진다 */
    QLabel {{ background: transparent; }}
    QLabel#dim {{ color: {TEXT_DIM}; }}
    /* 단계 배지 — 잠금/진행/완료를 색이 아니라 형태로도 구분한다.
       잠긴 것은 테두리만, 진행 중은 블루 채움, 끝난 것은 시안 채움 + 체크. */
    QLabel#stepNum {{
        color: {TEXT_DIM};
        border: 1px solid {BORDER_LINE};
        border-radius: 10px;
        font-size: 11px;
        font-weight: 600;
    }}
    QLabel#stepNumActive {{
        color: #FFFFFF;
        background: {ACCENT_BLUE};
        border: 1px solid {ACCENT_BLUE};
        border-radius: 10px;
        font-size: 11px;
        font-weight: 700;
    }}
    QLabel#stepNumDone {{
        color: #FFFFFF;
        background: {STATUS_OK_TEXT};
        border: 1px solid {STATUS_OK_TEXT};
        border-radius: 10px;
        font-size: 11px;
        font-weight: 700;
    }}

    /* 활성 버튼 — 앰버 채움 */
    QPushButton {{
        background: {ACCENT_BLUE};
        color: #FFFFFF;
        border: 1px solid {ACCENT_BLUE};
        border-radius: 6px;
        padding: 6px 14px;
        font-weight: 600;
    }}
    QPushButton:hover {{ background: #D97706; border-color: #D97706; }}
    QPushButton:pressed {{ background: #B45309; }}

    /* 비활성 버튼 — 앰버를 흐리지 않고 형태를 바꾼다.
       5단계 순차 잠금은 화면이 대부분의 시간을 이 상태로 보낸다. */
    QPushButton:disabled {{
        background: transparent;
        color: {TEXT_DIM};
        border: 1px solid {BORDER_LINE};
        font-weight: 500;
    }}

    QPushButton#ghost {{
        background: transparent;
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_LINE};
        font-weight: 500;
    }}
    QPushButton#ghost:hover {{ border-color: {TEXT_DIM}; }}
    QPushButton#ghost:disabled {{ color: {TEXT_DIM}; }}

    /* 뷰어 위에 뜨는 플로팅 HUD. 반투명 흰 카드 — 뒤의 3D·영상이 비친다 */
    QFrame#hud {{
        background: rgba(255, 255, 255, 0.94);
        border: 1px solid rgba(226, 232, 240, 0.9);
        border-radius: 10px;
    }}
    /* 뷰어 위에 떠 있는 버튼 */
    QPushButton#hudButton {{
        background: rgba(255, 255, 255, 0.94);
        color: {TEXT_PRIMARY};
        border: 1px solid rgba(226, 232, 240, 0.9);
        border-radius: 8px;
        padding: 6px 12px;
        font-weight: 500;
    }}
    QPushButton#hudButton:hover {{ border-color: {ACCENT_BLUE}; color: {ACCENT_BLUE}; }}

    QFrame#hudSep {{ background: {BORDER_LINE}; border: none; }}

    /* 좌표계 정합 경고 — 앰버 배너 */
    QFrame#banner {{
        background: rgba(254, 243, 199, 0.96);
        border: 1px solid {WARN_AMBER};
        border-radius: 10px;
    }}
    QLabel#bannerHead {{ color: #92400E; font-weight: 700; }}
    QLabel#bannerSub {{ color: #92400E; font-size: 11px; }}

    /* 탐지 목록 — 패널 안에 놓이므로 자기 배경·테두리를 갖지 않는다 */
    QListWidget#log {{
        background: transparent;
        border: none;
        font-size: 12px;
    }}
    QListWidget#log::item {{ color: {TEXT_PRIMARY}; padding: 2px 0; }}

    /* 모드 전환(세그먼트) — 좌우로 붙여 하나의 컨트롤로 보이게 한다.
       Qt QSS 에는 :first-child 가 없어서 objectName 으로 좌/우를 나눈다. */
    QPushButton#segLeft, QPushButton#segRight {{
        background: transparent;
        color: {TEXT_DIM};
        border: 1px solid {BORDER_LINE};
        border-radius: 0;
        padding: 6px 12px;
        font-weight: 500;
    }}
    QPushButton#segLeft {{
        border-top-left-radius: 6px;
        border-bottom-left-radius: 6px;
    }}
    QPushButton#segRight {{
        border-top-right-radius: 6px;
        border-bottom-right-radius: 6px;
        border-left: none;
    }}
    QPushButton#segLeft:hover, QPushButton#segRight:hover {{ color: {TEXT_PRIMARY}; }}
    QPushButton#segLeft:checked, QPushButton#segRight:checked {{
        background: {ACCENT_BLUE};
        color: #FFFFFF;
        border-color: {ACCENT_BLUE};
        font-weight: 600;
    }}

    /* 사이드바 네비게이션 */
    QFrame#sidebar {{
        background: {BG_PANEL};
        border: none;
        border-right: 1px solid {BORDER_LINE};
    }}
    QLabel#brand {{
        color: {TEXT_PRIMARY};
        font-size: 16px;
        font-weight: 700;
        padding: 4px 0 2px 3px;
    }}
    QLabel#brandSub {{
        color: {TEXT_DIM};
        font-size: 11px;
        padding: 0 0 8px 3px;
    }}
    /* 선택 표시는 좌측 앰버 바. 비선택도 같은 두께의 투명 테두리를 둬서
       선택 시 글자가 밀리지 않게 한다. */
    QPushButton#nav {{
        background: transparent;
        color: {TEXT_DIM};
        border: none;
        border-left: 3px solid transparent;
        border-radius: 0;
        padding: 10px 12px;
        text-align: left;
        font-weight: 500;
    }}
    QPushButton#nav:hover {{ color: {TEXT_PRIMARY}; background: {BG_MAIN}; }}
    QPushButton#nav:checked {{
        color: {TEXT_PRIMARY};
        background: {BG_MAIN};
        border-left: 3px solid {ACCENT_BLUE};
        font-weight: 600;
    }}

    /* 계기판 */
    QLabel#metricKey {{
        color: {TEXT_DIM};
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.5px;
    }}
    QLabel#metricValue {{
        color: {TEXT_PRIMARY};
        font-size: 19px;
        font-weight: 600;
    }}
    QLabel#chipValue {{
        color: {TEXT_PRIMARY};
        font-size: 16px;
        font-weight: 700;
    }}
    QLabel#metricSub {{
        color: {TEXT_DIM};
        font-size: 11px;
    }}

    QComboBox {{
        background: {BG_PANEL};
        border: 1px solid {BORDER_LINE};
        border-radius: 6px;
        padding: 5px 10px;
    }}
    QComboBox:focus {{ border-color: {ACCENT_BLUE}; }}
    QComboBox:disabled {{ background: transparent; color: {TEXT_DIM}; }}
    QComboBox QAbstractItemView {{
        background: {BG_PANEL};
        border: 1px solid {BORDER_LINE};
        selection-background-color: {ACCENT_BLUE};
        selection-color: #FFFFFF;
    }}

    QLineEdit, QSpinBox, QDoubleSpinBox {{
        background: {BG_PANEL};
        border: 1px solid {BORDER_LINE};
        border-radius: 6px;
        padding: 5px 8px;
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT_BLUE}; }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
        background: transparent;
        color: {TEXT_DIM};
    }}

    QFrame#video {{
        background: {VIEWPORT_BG};
        border: 1px solid {VIEWPORT_BG};
        border-radius: 8px;
    }}
    QLabel#videoText {{ color: {TEXT_DIM}; font-size: 13px; }}

    QProgressBar {{
        background: {BORDER_LINE};
        border: none;
        border-radius: 3px;
        min-height: 6px;
        max-height: 6px;
    }}
    QProgressBar::chunk {{ background: {ACCENT_BLUE}; border-radius: 3px; }}
    """
