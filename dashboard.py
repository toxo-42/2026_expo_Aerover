# dashboard.py
# 실행: streamlit run dashboard.py

import os, io, math, socket, struct, time, threading, contextlib, base64
import cv2, numpy as np
import streamlit as st

import run_mapping as rm
import run_detect as det
import telemetry as tm

PI_IP = "192.168.137.6"
PORT = 5001
SAVE_DIR = r"D:\Program\Code\Drone\received"
RECON_DIR = r"D:\Program\Code\Drone\recon3d"
ARCHIVE_DIR = r"D:\Program\Code\Drone\maps"        # 스티칭 결과 보관
TRASH_DIR = r"D:\Program\Code\Drone\maps\_trash"   # 삭제된 맵 (복원 가능)
DEFAULT_INTERVAL = 3.0

st.set_page_config(page_title="AeroVer", layout="wide",
                   page_icon="◆", initial_sidebar_state="collapsed")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;800&family=Barlow+Condensed:wght@500;600;700&display=swap');
:root{
  --bg:#0a0d12;
  --panel:#131922;
  --line:#232d3b;
  --line-hot:#33404f;
  --cyan:#38dcf0;
  --amber:#ffb340;
  --red:#ff6b73;
  --text:#eaf1f8;
  --dim:#8296ab;
  --vh:64vh;
}

#MainMenu, footer, header {visibility:hidden;}
.stApp {background:var(--bg);}
.block-container {padding:0.4rem 1rem 0.3rem 1rem !important; max-width:100% !important;}

/* 스크롤 차단 */
html, body{overflow:hidden !important;}
section[data-testid="stMain"]{overflow:hidden !important;}
div[data-testid="stElementContainer"]:has(div[data-testid="stImage"]),
div[data-testid="stElementContainer"]:has(.empty){
  overflow:hidden !important; margin:0 !important; line-height:0;
}
/* 빈 마크다운 문단이 여백을 만들지 않게 */
div[data-testid="stMarkdownContainer"] > p:empty{display:none;}

/* ---------- HUD 상단 바 ---------- */
.hud{
  display:flex; align-items:stretch;
  border:1px solid var(--line); border-radius:3px;
  background:linear-gradient(180deg,#101821 0%,#0c1219 100%);
  margin-bottom:7px; overflow:hidden;
}
.hud-cell{
  flex:1; padding:6px 15px; border-right:1px solid var(--line);
  display:flex; flex-direction:column; justify-content:center;
}
.hud-cell:last-child{border-right:none;}
.hud-k{
  font-family:'JetBrains Mono',monospace; font-size:.55rem;
  letter-spacing:.2em; color:#93a7bc; margin-bottom:4px;
}
.hud-v{
  font-family:'JetBrains Mono',monospace; font-weight:800;
  font-size:1.05rem; color:var(--text); line-height:1;
  display:flex; align-items:center; gap:8px;
}
.dot{width:7px;height:7px;border-radius:50%;flex:none;}
.dot.on {background:var(--cyan); box-shadow:0 0 9px var(--cyan); animation:bp 1.9s infinite;}
.dot.off{background:#3c4a59;}
.dot.act{background:var(--amber); box-shadow:0 0 9px var(--amber); animation:bp .9s infinite;}
@keyframes bp{0%,100%{opacity:1}50%{opacity:.32}}
@media (prefers-reduced-motion: reduce){.dot,.notice{animation:none !important}}

/* ---------- 탭 ---------- */
.stTabs [data-baseweb="tab-list"]{gap:0; border-bottom:1px solid var(--line);}
.stTabs [data-baseweb="tab"]{
  height:30px; padding:0 20px; border-radius:0;
  font-family:'JetBrains Mono',monospace; font-size:.68rem;
  letter-spacing:.13em; color:var(--dim);
  border-right:1px solid var(--line);
}
.stTabs [aria-selected="true"]{
  background:#111a23; color:var(--cyan); box-shadow:inset 0 -2px 0 var(--cyan);
}
.stTabs [data-baseweb="tab-highlight"]{display:none;}

/* ---------- 버튼 ---------- */
.stButton button, .stDownloadButton button{
  height:30px; border-radius:2px;
  font-family:'JetBrains Mono',monospace; font-size:.7rem;
  letter-spacing:.13em; font-weight:600;
  background:#111a23; color:var(--text); border:1px solid var(--line-hot);
  transition:border-color .15s, color .15s;
}
.stButton button:hover, .stDownloadButton button:hover{
  border-color:var(--cyan); color:var(--cyan);
}
.stButton button[kind="primary"]{
  background:#0c2229; color:var(--cyan); border:1px solid #1d5866;
}
.stButton button[kind="primary"]:hover{background:#0f2c35; border-color:var(--cyan);}
.stButton button:disabled{opacity:.32;}

/* ---------- 뷰포트 헤더 ---------- */
.vp-bar{
  display:flex; align-items:center; gap:10px;
  padding:5px 12px; background:#0b1118;
  border:1px solid var(--line); border-bottom:none; border-radius:3px 3px 0 0;
  font-family:'JetBrains Mono',monospace; font-size:.58rem;
  letter-spacing:.16em; color:#93a7bc;
}
.vp-bar .tag{color:var(--cyan);}
.vp-bar .sp{margin-left:auto;}

/* ---------- 뷰포트 본체 (이미지 자체가 박스) ---------- */
div[data-testid="stImage"]{
  margin:0 !important; width:100% !important;
  background:#05080c;
  border:1px solid var(--line); border-top:none;
  border-radius:0 0 3px 3px;
  background-image:
    linear-gradient(rgba(56,220,240,.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(56,220,240,.05) 1px, transparent 1px);
  background-size:44px 44px;
  height:var(--vh);
  display:flex; align-items:center; justify-content:center;
  overflow:hidden;
}
/* 실제 구조: stImage > stImageContainer > img */
div[data-testid="stImageContainer"]{
  width:100% !important; height:100% !important;
  margin:0 !important; padding:0 !important;
  display:flex !important; align-items:center; justify-content:center;
  overflow:hidden;
}
div[data-testid="stImage"] img{
  display:block; margin:0 auto !important;
  max-width:100% !important; max-height:100% !important;
  width:auto !important; height:auto !important;
  object-fit:contain;
  filter:saturate(1.10) contrast(1.05) brightness(1.04);
}

/* 빈 상태 */
.empty{
  height:var(--vh);
  display:flex; flex-direction:column; align-items:center; justify-content:center;
  gap:10px; font-family:'JetBrains Mono',monospace; letter-spacing:.16em;
  background:#05080c;
  border:1px solid var(--line); border-top:none; border-radius:0 0 3px 3px;
  background-image:
    linear-gradient(rgba(56,220,240,.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(56,220,240,.05) 1px, transparent 1px);
  background-size:44px 44px;
}
.empty .ic{font-size:1.6rem; color:#2e3d4c;}
.empty .tx{font-size:.68rem; color:var(--dim);}

/* ---------- 알림 배너 ---------- */
.notice{
  display:flex; align-items:center; gap:11px;
  padding:7px 13px; margin-bottom:7px; border-radius:3px;
  font-family:'JetBrains Mono',monospace; font-size:.66rem; letter-spacing:.11em;
  animation:slidein .35s cubic-bezier(.2,.9,.3,1);
}
@keyframes slidein{from{opacity:0;transform:translateY(-9px)}to{opacity:1;transform:none}}
.notice.go, .notice.done{
  background:#0a2129; border:1px solid #1d5866;
  border-left:3px solid var(--cyan); color:#bff3fc;
}
.notice.stop{
  background:#211a09; border:1px solid #56421d;
  border-left:3px solid var(--amber); color:#f5d9a5;
}
.notice .ic{font-size:.9rem;}

/* ---------- 수집 진행 ---------- */
.cap{
  border:1px solid var(--line); border-radius:3px;
  background:var(--panel); padding:8px 13px; margin-bottom:7px;
}
.cap-top{
  display:flex; align-items:baseline; gap:12px; margin-bottom:7px;
  font-family:'JetBrains Mono',monospace;
}
.cap-n{font-size:1.35rem; font-weight:800; color:var(--cyan); line-height:1;}
.cap-t{font-size:.9rem; color:var(--dim);}
.cap-lbl{font-size:.56rem; letter-spacing:.2em; color:var(--dim); margin-left:auto;}
.cap-track{display:flex; gap:3px; height:12px;}
.cap-seg{flex:1; border-radius:1px; background:#1c2733; min-width:2px;}
.cap-seg.f{background:var(--cyan); box-shadow:0 0 6px rgba(56,220,240,.45);}
.cap-seg.n{background:var(--amber); box-shadow:0 0 8px rgba(255,179,64,.6); animation:bp .7s infinite;}

/* ---------- 사이드 패널 ---------- */
.pnl{
  border:1px solid var(--line); border-radius:3px;
  background:var(--panel); margin-bottom:7px; overflow:hidden;
}
.pnl-h{
  padding:6px 11px; background:#0b1118; border-bottom:1px solid var(--line);
  font-family:'JetBrains Mono',monospace; font-size:.56rem;
  letter-spacing:.2em; color:#93a7bc;
}
.pnl-b{padding:7px 11px;}
.row{
  display:flex; justify-content:space-between; align-items:baseline;
  padding:3px 0; border-bottom:1px solid #1a2430;
  font-family:'JetBrains Mono',monospace;
}
.row:last-child{border-bottom:none;}
.row .k{font-size:.6rem; letter-spacing:.12em; color:#93a7bc;}
.row .v{font-size:.82rem; font-weight:600; color:var(--text);}
.row .v.cy{color:var(--cyan);}
.row .v.am{color:var(--amber);}
.row .v.rd{color:var(--red);}
.row .v.dim{color:#5d7186;}

/* ---------- 상태 그래픽 (SVG) ---------- */
.gfx{
  display:block; width:100%; height:118px;
  background:#080d13; border-bottom:1px solid var(--line);
}
.gfx text{font-family:'JetBrains Mono',monospace; fill:#93a7bc;}
.gfx .lbl{font-size:8px; letter-spacing:.1em;}
.gfx .big{font-size:11px; font-weight:600; fill:var(--cyan);}
.gfx .dim{font-size:9px; fill:#3c4a59; letter-spacing:.18em;}
.gfx .grid line, .gfx .grid circle{stroke:rgba(56,220,240,.16); stroke-width:.7; fill:none;}

/* 고도 뷰 */
.gfx .alt-line{
  stroke:var(--cyan); stroke-width:1; stroke-dasharray:3 3;
  animation:dashflow 1s linear infinite;
}
@keyframes dashflow{to{stroke-dashoffset:-12}}
.gfx .body{fill:var(--cyan);}
.gfx .arm{fill:var(--cyan); opacity:.6;}
.gfx .rotor{fill:var(--cyan); animation:rotor .4s ease-in-out infinite;}
@keyframes rotor{0%,100%{opacity:.2}50%{opacity:.7}}

/* 레이더 */
.gfx .sweep{
  transform-box:view-box; transform-origin:100px 59px;
  animation:sweep 4s linear infinite;
}
@keyframes sweep{to{transform:rotate(360deg)}}
.gfx .sweep line{stroke:var(--cyan); stroke-width:1.2; opacity:.5;}
.gfx .craft{fill:var(--amber);}
.gfx .home{fill:none; stroke:var(--cyan); stroke-width:1;}
.gfx .home-r{fill:none; stroke:var(--cyan); stroke-width:.7; opacity:.35;}

/* 배터리 */
.gfx .shell{fill:none; stroke:var(--line-hot); stroke-width:1.2;}
.gfx .cap{fill:var(--line-hot);}
.gfx .cell{fill:#1c2733;}
.gfx .cell.cy{fill:var(--cyan);}
.gfx .cell.am{fill:var(--amber);}
.gfx .cell.rd{fill:var(--red);}
.gfx .cell.lowpulse{animation:bp .9s infinite;}

@media (prefers-reduced-motion: reduce){
  .gfx *{animation:none !important;}
}

/* ---------- 수신 로그 ---------- */
.rx{border:1px solid var(--line); border-radius:3px; background:var(--panel); overflow:hidden;}
.rx-h{
  padding:6px 11px; background:#0b1118; border-bottom:1px solid var(--line);
  font-family:'JetBrains Mono',monospace; font-size:.56rem;
  letter-spacing:.2em; color:#93a7bc; display:flex; align-items:center; gap:7px;
}
.rx-r{
  display:flex; align-items:center; gap:7px; padding:3px 10px;
  border-bottom:1px solid #182129;
  font-family:'JetBrains Mono',monospace; font-size:.58rem; color:#93a7bc;
}
.rx-r:last-child{border-bottom:none;}
.rx-r.new{background:#0c1c22;}
.rx-r .f{color:var(--cyan); font-weight:600; min-width:48px;}
.rx-r .s{margin-left:auto; color:#5d7186;}
.rx-empty{
  padding:11px; text-align:center;
  font-family:'JetBrains Mono',monospace; font-size:.56rem; color:#3c4a59;
}

/* ---------- 위젯 ---------- */
div[data-testid="stNumberInput"] input{
  font-family:'JetBrains Mono',monospace !important;
  background:#0b1118 !important; color:var(--cyan) !important;
  border:1px solid var(--line-hot) !important; border-radius:2px;
  font-size:.8rem !important; font-weight:600;
}
div[data-testid="stNumberInput"] label p,
div[data-testid="stCheckbox"] label p{
  font-family:'JetBrains Mono',monospace !important;
  font-size:.6rem !important; letter-spacing:.14em; color:#93a7bc !important;
}
div[data-testid="stCheckbox"]{margin-top:-4px;}
div[data-testid="stExpander"]{
  border:1px solid var(--line) !important; border-radius:3px; background:var(--panel);
}
div[data-testid="stExpander"] summary p{
  font-family:'JetBrains Mono',monospace !important;
  font-size:.6rem !important; letter-spacing:.16em; color:#93a7bc !important;
}
.stCode, pre, code{
  font-family:'JetBrains Mono',monospace !important;
  font-size:.62rem !important; background:#070b0f !important;
  border:1px solid var(--line) !important;
}
div[data-testid="stAlert"]{
  background:#1e1418; border:1px solid #4a2830; border-left:2px solid var(--red);
  border-radius:2px; padding:.45rem .8rem;
  font-family:'JetBrains Mono',monospace; font-size:.66rem; color:#ffc4c9;
}
div[data-testid="stFileUploader"] section{
  background:var(--panel); border:1px dashed var(--line-hot); border-radius:3px;
}
div[data-testid="stCaptionContainer"] p{
  font-family:'JetBrains Mono',monospace !important;
  font-size:.58rem !important; letter-spacing:.12em; color:#93a7bc !important;
}

/* ---------- 맵 보관함 갤러리 ---------- */
.gal-h{
  padding:6px 11px; background:#0b1118; border:1px solid var(--line);
  border-bottom:none; border-radius:3px 3px 0 0;
  font-family:'JetBrains Mono',monospace; font-size:.56rem;
  letter-spacing:.2em; color:#93a7bc;
  display:flex; align-items:center; gap:7px;
}
.gal-empty{
  padding:14px 11px; text-align:center;
  border:1px solid var(--line); border-radius:0 0 3px 3px; background:var(--panel);
  font-family:'JetBrains Mono',monospace; font-size:.56rem; color:#3c4a59;
}

/* ---------- 탐지 집계 카드 ---------- */
.det-grid{display:flex; gap:7px; margin-bottom:7px;}
.det-card{
  flex:1; border:1px solid var(--line); border-radius:3px;
  background:var(--panel); padding:9px 11px; text-align:center;
}
.det-card .n{
  font-family:'JetBrains Mono',monospace; font-weight:800;
  font-size:1.6rem; line-height:1;
}
.det-card .l{
  font-family:'JetBrains Mono',monospace; font-size:.55rem;
  letter-spacing:.16em; color:#93a7bc; margin-top:5px;
}
.det-card.person{border-left:3px solid #ff5028;}
.det-card.person .n{color:#ff6b45;}
.det-card.car{border-left:3px solid #ffb340;}
.det-card.car .n{color:#ffc460;}
</style>
""", unsafe_allow_html=True)


# ---------- 공유 상태 ----------
if "worker" not in st.session_state:
    st.session_state.worker = {
        "run": False, "frame": None, "error": None, "last_rx": None,
        "save": False, "count": 0, "target": 0,
        "interval": DEFAULT_INTERVAL, "notice": None, "recent": [],
    }
W = st.session_state.worker

# 스레드 핸들은 세션 상태에 직접 못 담으므로 모듈 전역으로 보관
if "stream_thread" not in st.session_state:
    st.session_state.stream_thread = None

# 슬라이더 <-> 숫자입력 동기화
st.session_state.setdefault("n_interval", DEFAULT_INTERVAL)


def recv_exact(conn, n, state=None):
    """정확히 n바이트 수신. state["run"]이 내려가면 즉시 포기한다."""
    buf = b""
    while len(buf) < n:
        if state is not None and not state["run"]:
            return None
        try:
            c = conn.recv(n - len(buf))
        except socket.timeout:
            continue          # 1초마다 깨어나 중단 여부를 재확인
        except (ConnectionResetError, ConnectionAbortedError):
            return None       # Pi 가 연결을 끊음 — 조용히 종료
        except OSError:
            return None
        if not c:
            return None
        buf += c
    return buf


def stream_worker(state):
    """save=False면 프리뷰만, True면 INTERVAL마다 저장"""
    os.makedirs(SAVE_DIR, exist_ok=True)
    state["error"] = None
    try:
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.settimeout(3.0)
        conn.connect((PI_IP, PORT))
        conn.settimeout(1.0)   # 블로킹 방지 — 1초마다 중단 여부 확인
    except Exception as e:
        state["error"] = f"링크 연결 실패 — {e}"
        state["run"] = False
        return

    last = 0.0
    try:
        while state["run"]:
            hdr = recv_exact(conn, 4, state)
            if not hdr:
                break
            data = recv_exact(conn, struct.unpack("!I", hdr)[0], state)
            if data is None:
                break
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue

            state["frame"] = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            state["last_rx"] = time.time()

            if state["save"]:
                now = time.time()
                if now - last >= state["interval"]:
                    cnt = state["count"]
                    name = f"{cnt:03d}.jpg"
                    with open(os.path.join(SAVE_DIR, name), "wb") as f:
                        f.write(data)
                    last = now
                    state["count"] = cnt + 1
                    state["recent"].insert(0, (name, len(data), time.strftime("%H:%M:%S")))
                    del state["recent"][5:]
                    if state["target"] and state["count"] >= state["target"]:
                        state["save"] = False
                        state["notice"] = ("done",
                            f"수집 완료 — {state['count']}장을 저장했습니다")
    except Exception as e:
        state["error"] = f"수신 중단 — {e}"
    finally:
        with contextlib.suppress(Exception):
            conn.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(Exception):
            conn.close()
        state["run"] = False
        state["save"] = False


def browser_alive():
    """브라우저 세션이 살아 있는지 확인. 닫히면 False."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        from streamlit.runtime import get_instance
        ctx = get_script_run_ctx()
        if ctx is None:
            return False
        return get_instance().is_active_session(session_id=ctx.session_id)
    except Exception:
        return True     # 판별 불가 시 계속 진행


def stop_stream():
    """실행 중인 스트림을 내리고 스레드가 완전히 끝날 때까지 기다린다."""
    W["run"] = False
    W["save"] = False
    t = st.session_state.get("stream_thread")
    if t is not None and t.is_alive():
        t.join(timeout=3.0)      # 소켓이 닫히고 스레드가 종료되길 대기
    st.session_state.stream_thread = None


def ensure_stream():
    """스트림 스레드가 살아 있으면 그대로 두고, 없을 때만 새로 띄운다."""
    t = st.session_state.get("stream_thread")
    if W["run"] and t is not None and t.is_alive():
        return                      # 이미 돌고 있다 — 재시작하지 않는다

    # 죽은 스레드 흔적이 남아 있으면 정리
    if t is not None and t.is_alive():
        W["run"] = False
        t.join(timeout=3.0)

    W["run"] = True
    W["error"] = None
    W["frame"] = None
    W["last_rx"] = None
    th = threading.Thread(target=stream_worker, args=(W,), daemon=True)
    th.start()
    st.session_state.stream_thread = th


def list_jpg(d):
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.lower().endswith(".jpg"))


def save_to_archive(src_path):
    """스티칭 결과를 타임스탬프 이름으로 maps 폴더에 복사한다."""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    name = time.strftime("map_%Y%m%d_%H%M%S.jpg")
    dst = os.path.join(ARCHIVE_DIR, name)
    import shutil
    shutil.copy(src_path, dst)
    return name


def list_archive():
    """보관된 맵 목록을 최신순으로 반환한다."""
    if not os.path.isdir(ARCHIVE_DIR):
        return []
    fs = [f for f in os.listdir(ARCHIVE_DIR) if f.lower().endswith(".jpg")]
    fs.sort(reverse=True)      # 파일명이 타임스탬프라 역순 = 최신순
    return fs


def delete_archive(name):
    """보관된 맵을 완전히 지우지 않고 휴지통(_trash)으로 옮긴다."""
    import shutil
    os.makedirs(TRASH_DIR, exist_ok=True)
    src = os.path.join(ARCHIVE_DIR, name)
    dst = os.path.join(TRASH_DIR, name)
    # 같은 이름이 이미 휴지통에 있으면 뒤에 번호를 붙인다
    if os.path.exists(dst):
        base, ext = os.path.splitext(name)
        k = 1
        while os.path.exists(os.path.join(TRASH_DIR, f"{base}_{k}{ext}")):
            k += 1
        dst = os.path.join(TRASH_DIR, f"{base}_{k}{ext}")
    with contextlib.suppress(OSError):
        shutil.move(src, dst)


def list_trash():
    """휴지통에 있는 맵 목록을 최신순으로 반환한다."""
    if not os.path.isdir(TRASH_DIR):
        return []
    fs = [f for f in os.listdir(TRASH_DIR) if f.lower().endswith(".jpg")]
    fs.sort(reverse=True)
    return fs


def restore_from_trash(name):
    """휴지통의 맵을 보관함으로 되돌린다."""
    import shutil
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    src = os.path.join(TRASH_DIR, name)
    dst = os.path.join(ARCHIVE_DIR, name)
    if os.path.exists(dst):
        base, ext = os.path.splitext(name)
        k = 1
        while os.path.exists(os.path.join(ARCHIVE_DIR, f"{base}_{k}{ext}")):
            k += 1
        dst = os.path.join(ARCHIVE_DIR, f"{base}_{k}{ext}")
    with contextlib.suppress(OSError):
        shutil.move(src, dst)


def purge_trash(name):
    """휴지통의 맵을 영구 삭제한다."""
    with contextlib.suppress(OSError):
        os.remove(os.path.join(TRASH_DIR, name))


def pretty_map_name(name):
    """map_20260724_161230.jpg -> 26-07-24 16:12"""
    try:
        core = name.replace("map_", "").replace(".jpg", "")
        d, t = core.split("_")
        return f"{d[2:4]}-{d[4:6]}-{d[6:8]} {t[0:2]}:{t[2:4]}"
    except Exception:
        return name


def _rows_html(rows):
    return "".join(
        f'<div class="row"><span class="k">{k}</span>'
        f'<span class="v {c}">{v}</span></div>'
        for k, v, c in rows
    )


def panel(title, rows):
    """rows: [(라벨, 값, 색클래스)] — 색클래스는 "", "cy", "am" """
    head = f'<div class="pnl-h">{title}</div>' if title else ""
    st.markdown(
        f'<div class="pnl">{head}'
        f'<div class="pnl-b">{_rows_html(rows)}</div></div>',
        unsafe_allow_html=True)


def panel_gfx(title, gfx, rows):
    """상단에 그래픽이 붙은 panel()"""
    st.markdown(
        f'<div class="pnl"><div class="pnl-h">{title}</div>{gfx}'
        f'<div class="pnl-b">{_rows_html(rows)}</div></div>',
        unsafe_allow_html=True)


def empty_state(icon, text):
    st.markdown(
        f'<div class="empty"><div class="ic">{icon}</div>'
        f'<div class="tx">{text}</div></div>',
        unsafe_allow_html=True)


def lvl(ok, warn):
    """정상/주의/위험을 panel() 색 클래스로"""
    return "cy" if ok else ("am" if warn else "rd")


_DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def compass(deg):
    return _DIRS[int((deg + 22.5) % 360 // 45)]


def fmt_age(a):
    return "—" if a is None else f"{a:.1f}s"


def age_lvl(a, limit):
    return lvl(a is not None and a < limit, a is not None and a < limit * 3)


# ---------- 상태 그래픽 ----------

_SVG = ('<svg class="gfx" viewBox="0 0 200 118" '
        'preserveAspectRatio="xMidYMid meet">{}</svg>')


def _phase(period):
    """음수 delay로 애니메이션 위상을 벽시계에 고정한다.
    fragment가 0.5초마다 DOM을 새로 그려도 이어지는 것처럼 보인다."""
    return f"animation-delay:-{time.time() % period:.2f}s;"


def _gfx_empty(text):
    return _SVG.format(
        f'<text x="100" y="63" text-anchor="middle" class="dim">{text}</text>')


def gfx_altitude(agl, target):
    """기체가 그리드 바닥 위로 떠오르고, 점선으로 고도를 표시"""
    if agl is None:
        return _gfx_empty("NO FIX")

    ratio = max(0.0, min(1.3, agl / target if target else 0.0))
    dy = 70 - 42 * (ratio / 1.3)       # 기체 y (70=지면, 28=최고)
    k = 1 + 0.55 * (ratio / 1.3)       # 고도↑ → 그리드가 넓어짐
    horizon = 74

    g = [f'<line x1="100" y1="{horizon}" x2="{100 + u * 96 * k:.1f}" y2="118"/>'
         for u in (-1, -0.55, 0, 0.55, 1)]
    for t in (0.18, 0.38, 0.64, 1.0):
        y, hw = horizon + 44 * t, 96 * k * t
        g.append(f'<line x1="{100 - hw:.1f}" y1="{y:.1f}" '
                 f'x2="{100 + hw:.1f}" y2="{y:.1f}"/>')

    return _SVG.format(
        f'<g class="grid">{"".join(g)}</g>'
        f'<line class="alt-line" x1="100" y1="{dy + 7:.1f}" x2="100" y2="{horizon}" '
        f'style="{_phase(1.0)}"/>'
        f'<g transform="translate(100,{dy:.1f})">'
        f'<rect x="-16" y="-1" width="32" height="2" class="arm"/>'
        f'<rect x="-6" y="-4" width="12" height="7" rx="1" class="body"/>'
        f'<ellipse cx="-16" cy="-3" rx="7.5" ry="1.8" class="rotor" '
        f'style="{_phase(0.4)}"/>'
        f'<ellipse cx="16" cy="-3" rx="7.5" ry="1.8" class="rotor" '
        f'style="{_phase(0.4)}"/></g>'
        f'<text x="108" y="{(dy + horizon) / 2 + 4:.1f}" class="big">{agl} m</text>')


def gfx_radar(dist, bearing, heading):
    """이륙지점을 중심으로 기체 위치를 방위·거리로 표시"""
    if dist is None:
        return _gfx_empty("NO FIX")

    span = 1600
    for s in (25, 50, 100, 200, 400, 800):
        if s >= max(dist, 8) * 1.15:
            span = s
            break

    cx, cy, rmax = 100, 59, 52
    rings = "".join(f'<circle cx="{cx}" cy="{cy}" r="{rmax * f:.1f}"/>'
                    for f in (0.33, 0.66, 1.0))
    cross = (f'<line x1="{cx - rmax}" y1="{cy}" x2="{cx + rmax}" y2="{cy}"/>'
             f'<line x1="{cx}" y1="{cy - rmax}" x2="{cx}" y2="{cy + rmax}"/>')

    r = min(rmax, dist / span * rmax)
    a = math.radians(bearing)
    px, py = cx + math.sin(a) * r, cy - math.cos(a) * r

    return _SVG.format(
        f'<g class="grid">{rings}{cross}</g>'
        f'<g class="sweep" style="{_phase(4.0)}">'
        f'<line x1="{cx}" y1="{cy}" x2="{cx}" y2="{cy - rmax}"/></g>'
        f'<circle cx="{cx}" cy="{cy}" r="3" class="home"/>'
        f'<circle cx="{cx}" cy="{cy}" r="6" class="home-r"/>'
        f'<g transform="translate({px:.1f},{py:.1f}) rotate({heading:.0f})">'
        f'<polygon points="0,-5 3.4,4 0,2 -3.4,4" class="craft"/></g>'
        f'<text x="196" y="114" text-anchor="end" class="lbl">R {span} m</text>')


def gfx_battery(pct, volt):
    """100/75/50/25/0 기준으로 4칸이 한 칸씩 줄어든다"""
    filled = max(0, min(4, math.ceil(pct / 25)))
    color = "cy" if pct >= 50 else ("am" if pct >= 25 else "rd")
    x0, y0, w, h, gap = 40, 38, 27, 34, 4

    cells = []
    for i in range(4):
        x = x0 + i * (w + gap)
        if i < filled:
            low = " lowpulse" if pct <= 25 else ""
            style = f' style="{_phase(0.9)}"' if low else ""
            cells.append(f'<rect x="{x}" y="{y0}" width="{w}" height="{h}" '
                         f'rx="2" class="cell {color}{low}"{style}/>')
        else:
            cells.append(f'<rect x="{x}" y="{y0}" width="{w}" height="{h}" '
                         f'rx="2" class="cell"/>')

    return _SVG.format(
        f'<rect x="{x0 - 5}" y="{y0 - 5}" width="{4 * w + 3 * gap + 10}" '
        f'height="{h + 10}" rx="3" class="shell"/>'
        f'<rect x="{x0 + 4 * w + 3 * gap + 6}" y="{y0 + h / 2 - 7}" '
        f'width="5" height="14" rx="1.5" class="cap"/>'
        f'{"".join(cells)}'
        f'<text x="100" y="98" text-anchor="middle" class="big">'
        f'{volt:.1f} V · {pct}%</text>')


def render_cap(w):
    """수집 진행 세그먼트 바 HTML"""
    tgt = max(w["target"], 1)
    done = min(w["count"], tgt)
    segs = []
    for i in range(tgt):
        cls = "f" if i < done - 1 else ("n" if i == done - 1 else "")
        segs.append(f'<div class="cap-seg {cls}"></div>')
    pct = int(done / tgt * 100)
    return (f'<div class="cap"><div class="cap-top">'
            f'<span class="cap-n">{done}</span>'
            f'<span class="cap-t">/ {tgt}</span>'
            f'<span class="cap-lbl">{pct}% · {w["interval"]:.1f}s 간격</span>'
            f'</div><div class="cap-track">{"".join(segs)}</div></div>')


def render_rx(w):
    """수신 프레임 로그 HTML"""
    rows = list(w["recent"])
    if rows:
        body = "".join(
            f'<div class="rx-r {"new" if i == 0 else ""}">'
            f'<span class="f">{n}</span>'
            f'<span>{sz / 1024:.0f} KB</span>'
            f'<span class="s">{t}</span></div>'
            for i, (n, sz, t) in enumerate(rows))
    else:
        body = '<div class="rx-empty">수신된 프레임이 없습니다</div>'
    return (f'<div class="rx"><div class="rx-h">'
            f'<span class="dot {"act" if w["save"] else "off"}"></span>'
            f'INBOUND FRAMES</div>{body}</div>')


# ---------- HUD ----------
alive = bool(W["run"] and W["last_rx"] and (time.time() - W["last_rx"] < 3))
files = list_jpg(SAVE_DIR)
mapped = os.path.exists(rm.OUT_PATH)

link_dot = "on" if alive else "off"
link_tx = "LINKED" if alive else "NO SIGNAL"
cap_dot = "act" if W["save"] else ("on" if W["run"] else "off")
cap_tx = "CAPTURING" if W["save"] else ("STANDBY" if W["run"] else "IDLE")

st.markdown(f"""
<div class="hud">
  <div class="hud-cell">
    <div class="hud-k">DOWNLINK</div>
    <div class="hud-v"><span class="dot {link_dot}"></span>{link_tx}</div>
  </div>
  <div class="hud-cell">
    <div class="hud-k">CAPTURE</div>
    <div class="hud-v"><span class="dot {cap_dot}"></span>{cap_tx}</div>
  </div>
  <div class="hud-cell">
    <div class="hud-k">SESSION</div>
    <div class="hud-v">{W['count']:03d}</div>
  </div>
  <div class="hud-cell">
    <div class="hud-k">FRAMES ON DISK</div>
    <div class="hud-v">{len(files):03d}</div>
  </div>
  <div class="hud-cell">
    <div class="hud-k">MAP</div>
    <div class="hud-v"><span class="dot {'on' if mapped else 'off'}"></span>{'READY' if mapped else '—'}</div>
  </div>
</div>
""", unsafe_allow_html=True)

if W["error"]:
    st.error(W["error"])


tab_status, tab_cam, tab_2d, tab_det, tab_3d = st.tabs(
    ["드론 상태", "실시간 카메라", "매핑", "탐지", "3D 복원"])


# ===== 1. 실시간 카메라 =====
with tab_cam:
    # --- 알림 배너 (화면 중앙 상단) ---
    notice_slot = st.empty()
    if W["notice"]:
        kind, msg = W["notice"]
        icon = {"go": "▶", "done": "✔", "stop": "■"}.get(kind, "•")
        notice_slot.markdown(
            f'<div class="notice {kind}"><span class="ic">{icon}</span>{msg}</div>',
            unsafe_allow_html=True)

    # --- 수집 진행 (세그먼트 바) ---
    cap_slot = st.empty()
    if W["target"]:
        cap_slot.markdown(render_cap(W), unsafe_allow_html=True)

    main, side = st.columns([3.3, 1], gap="medium")

    with side:
        panel("CAPTURE CONTROL", [
            ("소스", f"{PI_IP}:{PORT}", "cy"),
            ("촬영 간격", f"{W['interval']:.1f}s", ""),
            ("목표 장수", f"{W['target'] or '—'}", ""),
        ])

        b1, b2 = st.columns(2)
        if b1.button("링크 개통", disabled=W["run"], type="primary",
                     width="stretch"):
            W["save"] = False      # 프리뷰만 — 저장하지 않는다
            W["target"] = 0        # 이전 세션의 목표치를 지운다
            W["recent"] = []
            W["notice"] = ("go", "링크를 개통했습니다 — 실시간 영상 수신 중")
            ensure_stream()
            st.rerun()
        if b2.button("링크 차단", disabled=not W["run"], width="stretch"):
            stop_stream()
            W["notice"] = ("stop", "링크를 차단했습니다")
            st.rerun()

        n1, n2 = st.columns(2)
        n1.number_input("수집 장수", min_value=1, max_value=300, step=1,
                        value=None, placeholder="장수 입력", key="nm_shots")
        n2.number_input("간격 (초)", min_value=0.5, max_value=30.0, step=0.5,
                        key="n_interval", format="%.1f")

        st.checkbox("수집 전 기존 이미지 삭제", value=True, key="clear_first")

        b3, b4 = st.columns(2)
        if b3.button("정찰 개시", type="primary", width="stretch",
                     disabled=W["save"]):
            shots = st.session_state.get("nm_shots")
            if not shots:
                W["notice"] = ("stop", "수집 장수를 입력하세요")
            else:
                n_removed = 0
                if st.session_state.get("clear_first", True):
                    for f in list_jpg(SAVE_DIR):
                        os.remove(os.path.join(SAVE_DIR, f))
                        n_removed += 1
                    W["count"] = 0
                W["recent"] = []
                W["target"] = int(shots)
                iv = st.session_state.get("n_interval")
                W["interval"] = float(iv) if iv else DEFAULT_INTERVAL
                W["save"] = True
                tail = f" · 기존 {n_removed}장 삭제" if n_removed else ""
                W["notice"] = ("go",
                               f"정찰을 개시합니다 — {W['target']}장 · "
                               f"{W['interval']:.1f}초 간격{tail}")
                ensure_stream()
            st.rerun()
        if b4.button("정찰 중단", disabled=not W["save"], width="stretch"):
            W["save"] = False      # 저장만 멈춘다 — 진행 상황은 그대로 남긴다
            W["notice"] = ("stop", f"정찰을 중단했습니다 — {W['count']}장 저장됨")
            st.rerun()

        rx_slot = st.empty()
        rx_slot.markdown(render_rx(W), unsafe_allow_html=True)

    with main:
        st.markdown(f"""
        <div class="vp-bar">
          <span class="tag">◉ LIVE</span>
          <span>NADIR / CAM-01</span>
          <span class="sp">{'STREAMING' if W['run'] else 'OFFLINE'}</span>
        </div>""", unsafe_allow_html=True)
        holder = st.empty()
        if not W["run"]:
            with holder:
                empty_state("◎", "링크 개통을 눌러 카메라를 연결합니다")


# ===== 2. 매핑 =====
with tab_2d:
    main, side = st.columns([3.3, 1], gap="medium")

    passed = st.session_state.get("passed")
    archive = list_archive()

    # 어떤 맵을 뷰어에 띄울지: 보관함 선택본 우선, 없으면 방금 만든 결과
    sel = st.session_state.get("sel_map")
    if sel and sel not in archive:
        sel = None
        st.session_state.sel_map = None

    if sel:
        view_path = os.path.join(ARCHIVE_DIR, sel)
        view_label = pretty_map_name(sel)
    elif mapped:
        view_path = rm.OUT_PATH
        view_label = "최신 결과"
    else:
        view_path = None
        view_label = "—"

    with side:
        panel("MAPPING PIPELINE", [
            ("입력 프레임", f"{len(files)}", ""),
            ("필터 통과", f"{len(passed)}" if passed is not None else "—",
             "cy" if passed else ""),
            ("보관된 맵", f"{len(archive)}", "cy" if archive else ""),
        ])

        if st.button("프레임 필터링", width="stretch"):
            buf = io.StringIO()
            with st.spinner("필터링 중"), contextlib.redirect_stdout(buf):
                st.session_state.passed = rm.run_filter()
            st.session_state.log2d = buf.getvalue()
            n = len(st.session_state.passed)
            st.session_state.map_notice = (
                ("done", f"{n}장이 필터를 통과했습니다") if n
                else ("stop", "통과한 프레임이 없습니다 — 촬영 조건을 확인하세요"))
            st.rerun()

        if st.button("맵 생성", type="primary", width="stretch"):
            if not passed:
                st.session_state.map_notice = ("stop", "프레임 필터링을 먼저 실행하세요")
            else:
                buf = io.StringIO()
                with st.spinner("맵 생성 중"), contextlib.redirect_stdout(buf):
                    rm.run_stitch(passed)
                st.session_state.log2d = buf.getvalue()
                # 생성 즉시 보관함에 자동 저장
                if os.path.exists(rm.OUT_PATH):
                    saved = save_to_archive(rm.OUT_PATH)
                    st.session_state.sel_map = None      # 최신 결과를 본다
                    st.session_state.map_notice = (
                        "done", f"맵 생성 완료 — 보관함에 저장됨 ({pretty_map_name(saved)})")
                else:
                    st.session_state.map_notice = ("stop", "맵 생성에 실패했습니다")
            st.rerun()

        if view_path and os.path.exists(view_path):
            with open(view_path, "rb") as f:
                st.download_button("현재 맵 내려받기", f.read(),
                                   "aerover_map.jpg", "image/jpeg",
                                   width="stretch")

        # ---- 맵 보관함 갤러리 ----
        st.markdown(
            '<div class="gal-h"><span class="dot on"></span>'
            'MAP ARCHIVE</div>', unsafe_allow_html=True)
        if archive:
            for name in archive[:8]:
                is_sel = (name == sel)
                gc1, gc2 = st.columns([4, 1])
                label = ("● " if is_sel else "") + pretty_map_name(name)
                if gc1.button(label, key=f"open_{name}", width="stretch",
                              type="primary" if is_sel else "secondary"):
                    st.session_state.sel_map = name
                    st.rerun()
                if gc2.button("✕", key=f"del_{name}", width="stretch"):
                    delete_archive(name)
                    if st.session_state.get("sel_map") == name:
                        st.session_state.sel_map = None
                    st.rerun()
        else:
            st.markdown('<div class="gal-empty">보관된 맵이 없습니다</div>',
                        unsafe_allow_html=True)

        # ---- 휴지통 ----
        trash = list_trash()
        with st.expander(f"휴지통 ({len(trash)})"):
            if trash:
                for name in trash[:8]:
                    tc1, tc2 = st.columns([3, 2])
                    tc1.markdown(
                        f'<div style="font-family:JetBrains Mono,monospace;'
                        f'font-size:.6rem;color:#93a7bc;padding-top:6px">'
                        f'{pretty_map_name(name)}</div>',
                        unsafe_allow_html=True)
                    r1, r2 = tc2.columns(2)
                    if r1.button("복원", key=f"rst_{name}", width="stretch"):
                        restore_from_trash(name)
                        st.session_state.map_notice = ("done", "맵을 복원했습니다")
                        st.rerun()
                    if r2.button("영구삭제", key=f"prg_{name}", width="stretch"):
                        purge_trash(name)
                        st.rerun()
            else:
                st.caption("휴지통이 비어 있습니다")

        with st.expander("처리 로그"):
            st.code(st.session_state.get("log2d", "로그 없음"), language=None)

    with main:
        mn = st.session_state.get("map_notice")
        if mn:
            kind, msg = mn
            icon = {"done": "✔", "stop": "■"}.get(kind, "•")
            st.markdown(
                f'<div class="notice {kind}"><span class="ic">{icon}</span>'
                f'{msg}</div>', unsafe_allow_html=True)

        wh = "—"
        if view_path and os.path.exists(view_path):
            probe = cv2.imread(view_path, cv2.IMREAD_REDUCED_COLOR_8)
            if probe is not None:
                wh = f"{probe.shape[1] * 8} × {probe.shape[0] * 8}"
        st.markdown(f"""
        <div class="vp-bar">
          <span class="tag">◈ MAP</span>
          <span>{view_label}</span>
          <span class="sp">{wh}</span>
        </div>""", unsafe_allow_html=True)
        if view_path and os.path.exists(view_path):
            st.image(view_path)
        else:
            empty_state("◇", "필터링 후 맵 생성을 실행하세요")


# ===== 3. 탐지 =====
with tab_det:
    main, side = st.columns([3.3, 1], gap="medium")

    det_result = st.session_state.get("det_result")   # {"n_person":.., "n_car":.., "path":..}

    with side:
        n_person = det_result["n_person"] if det_result else 0
        n_car = det_result["n_car"] if det_result else 0

        st.markdown(f"""
        <div class="det-grid">
          <div class="det-card person"><div class="n">{n_person}</div>
            <div class="l">PERSON</div></div>
          <div class="det-card car"><div class="n">{n_car}</div>
            <div class="l">VEHICLE</div></div>
        </div>""", unsafe_allow_html=True)

        panel("DETECTION", [
            ("입력 프레임", f"{len(files)}", ""),
            ("검출 상태", "완료" if det_result else "대기",
             "cy" if det_result else ""),
            ("모델", "YOLOv11n", ""),
        ])

        if st.button("탐지 실행", type="primary", width="stretch"):
            src = list_jpg(SAVE_DIR)
            if not src:
                st.session_state.det_notice = ("stop", "탐지할 이미지가 없습니다")
            else:
                total_p = total_c = 0
                boxed = []
                buf = io.StringIO()
                with st.spinner("객체 탐지 중"):
                    for fn in src:
                        p = os.path.join(SAVE_DIR, fn)
                        img = cv2.imread(p)
                        if img is None:
                            continue
                        dets = det.detect(p)
                        p_cnt, c_cnt = det.count_classes(dets)
                        total_p += p_cnt
                        total_c += c_cnt
                        img = det.draw_boxes(img, dets)
                        boxed.append(img)

                # 박스 그린 이미지들을 임시 저장 후 스티칭
                det_dir = os.path.join(SAVE_DIR, "_det")
                os.makedirs(det_dir, exist_ok=True)
                for old in list_jpg(det_dir):
                    with contextlib.suppress(OSError):
                        os.remove(os.path.join(det_dir, old))
                det_paths = []
                for i, im in enumerate(boxed):
                    dp = os.path.join(det_dir, f"{i:03d}.jpg")
                    cv2.imwrite(dp, im)
                    det_paths.append(dp)

                out_path = os.path.join(SAVE_DIR, "_det_map.jpg")
                if len(det_paths) >= 2:
                    with contextlib.redirect_stdout(buf):
                        res = rm.stitch_opencv(det_paths)
                        if res is None:
                            res = rm.stitch_sequential(det_paths)
                    if res is not None:
                        cv2.imwrite(out_path, rm.crop_black(res))
                elif det_paths:
                    cv2.imwrite(out_path, cv2.imread(det_paths[0]))

                st.session_state.det_result = {
                    "n_person": total_p, "n_car": total_c,
                    "path": out_path if os.path.exists(out_path) else None,
                }
                if total_p + total_c == 0:
                    st.session_state.det_notice = (
                        "stop", "검출된 객체가 없습니다 — YOLO 모델 연결을 확인하세요")
                else:
                    st.session_state.det_notice = (
                        "done", f"탐지 완료 — 사람 {total_p}, 차량 {total_c}")
            st.rerun()

        with st.expander("탐지 로그"):
            st.code("YOLO 모델 미연결 상태입니다.\n"
                    "detect() 함수에 팀원 코드를 넣으면 동작합니다.",
                    language=None)

    with main:
        dn = st.session_state.get("det_notice")
        if dn:
            kind, msg = dn
            icon = {"done": "✔", "stop": "■"}.get(kind, "•")
            st.markdown(
                f'<div class="notice {kind}"><span class="ic">{icon}</span>'
                f'{msg}</div>', unsafe_allow_html=True)

        det_path = det_result["path"] if det_result else None
        st.markdown(f"""
        <div class="vp-bar">
          <span class="tag">◎ DETECT</span>
          <span>SURVIVOR / VEHICLE</span>
          <span class="sp">{'MAPPED' if det_path else 'IDLE'}</span>
        </div>""", unsafe_allow_html=True)
        if det_path and os.path.exists(det_path):
            st.image(det_path)
        else:
            empty_state("◎", "탐지 실행을 눌러 객체를 검출하세요")


# ===== 4. 3D 복원 =====
with tab_3d:
    main, side = st.columns([3.3, 1], gap="medium")

    with side:
        panel("RECONSTRUCTION", [
            ("입력 프레임", f"{len(files)}", ""),
            ("출력 경로", "recon3d", ""),
            ("엔진", "대기", ""),
        ])
        st.button("복원 실행", type="primary", width="stretch", disabled=True)
        st.button("모델 로드", width="stretch", disabled=True)
        up = st.file_uploader("GLB 업로드", type=["glb"])
        with st.expander("복원 로그"):
            st.code(st.session_state.get("log3d", "로그 없음"), language=None)

    with main:
        st.markdown("""
        <div class="vp-bar">
          <span class="tag">◈ 3D</span>
          <span>POINT CLOUD / MESH</span>
          <span class="sp">RECON_3D</span>
        </div>""", unsafe_allow_html=True)
        if up:
            b64 = base64.b64encode(up.read()).decode()
            st.components.v1.html(f"""
            <script type="module"
              src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.5.0/model-viewer.min.js"></script>
            <model-viewer src="data:model/gltf-binary;base64,{b64}"
                camera-controls auto-rotate
                style="width:100%;height:560px;background:transparent;">
            </model-viewer>
            """, height=570)
        else:
            empty_state("◈", "GLB 모델을 업로드하거나 복원을 실행하세요")


# ===== 5. 드론 상태 =====
with tab_status:
    st.markdown(f"""
    <div class="vp-bar">
      <span class="tag">◉ TELEMETRY</span>
      <span>CRSF · {'DUMMY' if tm.USE_DUMMY else 'POCKET / USB-VCP'}</span>
      <span class="sp">0x02 · 0x08 · 0x14</span>
    </div>""", unsafe_allow_html=True)

    @st.fragment(run_every=0.5)
    def telemetry_view():
        """스크립트 전체가 아니라 이 함수만 0.5초마다 재실행된다"""
        snap = tm.get_telemetry()
        g, b, k = snap["gps"], snap["battery"], snap["link"]
        fix = g["sats"] > 0
        status = tm.link_status(snap)

        agl = g["alt_m"] - tm.HOME_ALT_M if fix else None    # 해발 → 지상고
        dist = tm.distance_from_home(snap)
        target_m = rm.TARGET_ALT_CM / 100                    # 매핑 기준과 동일

        c1, c2, c3 = st.columns(3, gap="medium")
        with c1:
            panel_gfx("FLIGHT", gfx_altitude(agl, target_m), [
                ("고도 (AGL)",
                 f"{agl} m" if agl is not None else "—",
                 lvl(agl is not None and abs(agl - target_m) <= target_m * 0.10,
                     agl is not None and abs(agl - target_m) <= target_m * 0.25)),
                ("대지속도", f"{g['speed_kmh']:.1f} km/h" if fix else "—", ""),
                ("헤딩",
                 f"{g['heading']:.0f}° {compass(g['heading'])}" if fix else "—",
                 ""),
            ])
        with c2:
            panel_gfx("POSITION",
                      gfx_radar(dist, tm.bearing_from_home(snap), g["heading"]), [
                ("위도", f"{g['lat']:.6f}" if fix else "—", "" if fix else "dim"),
                ("경도", f"{g['lon']:.6f}" if fix else "—", "" if fix else "dim"),
                ("이륙지점 거리",
                 f"{dist:.0f} m" if dist is not None else "—", ""),
            ])
        with c3:
            panel_gfx("SYSTEM",
                      gfx_battery(b["remaining_pct"], b["voltage"]), [
                ("GPS 위성", f"{g['sats']}" if fix else "NO FIX",
                 lvl(g["sats"] >= 10, g["sats"] >= rm.MIN_SATS)),
                ("배터리", f"{b['voltage']:.1f} V · {b['remaining_pct']}%",
                 lvl(b["remaining_pct"] >= 40, b["remaining_pct"] >= 20)),
                ("링크", status, lvl(status == "OK", status == "WEAK")),
            ])

        panel("LINK QUALITY", [
            ("업링크 LQ", f"{k['up_lq']} %",
             lvl(k["up_lq"] >= 80, k["up_lq"] >= 50)),
            ("업링크 RSSI", f"{k['up_rssi1']} dBm",
             lvl(k["up_rssi1"] >= -85, k["up_rssi1"] >= -100)),
            ("업링크 SNR", f"{k['up_snr']} dB", ""),
            ("송신 출력", f"idx {k['tx_power_idx']}", ""),
        ])

        # 어느 프레임이 막혔는지 여기서 바로 보인다
        a_gps, a_bat, a_lnk = (tm.age(snap, x)
                               for x in ("gps", "battery", "link"))
        with st.expander("DATA AGE"):
            panel("", [
                ("GPS (0x02)", fmt_age(a_gps), age_lvl(a_gps, 2.0)),
                ("BATTERY (0x08)", fmt_age(a_bat), age_lvl(a_bat, 2.0)),
                ("LINK (0x14)", fmt_age(a_lnk), age_lvl(a_lnk, 0.3)),
            ])

    telemetry_view()


# ---------- 프리뷰 루프 ----------
if W["run"]:
    last_count = -1
    was_saving = W["save"]     # 수집 종료를 감지하기 위한 기준값
    tick = 0
    try:
        while W["run"]:
            # 목표 장수를 채워 수집이 끝났으면 화면을 즉시 갱신한다
            if was_saving and not W["save"]:
                break

            # 브라우저가 닫혔으면 링크를 내린다 (약 1초마다 확인)
            tick += 1
            if tick % 20 == 0 and not browser_alive():
                stop_stream()
                break

            if W["frame"] is not None:
                holder.image(W["frame"])

            # 새 프레임이 저장될 때만 진행 표시를 갱신
            if W["count"] != last_count:
                last_count = W["count"]
                if W["target"]:
                    cap_slot.markdown(render_cap(W), unsafe_allow_html=True)
                rx_slot.markdown(render_rx(W), unsafe_allow_html=True)

            time.sleep(0.05)
    except Exception:
        # 브라우저 연결이 끊기면 위젯 쓰기가 실패한다 — 링크를 정리한다
        W["run"] = False
        W["save"] = False
    else:
        st.rerun()
