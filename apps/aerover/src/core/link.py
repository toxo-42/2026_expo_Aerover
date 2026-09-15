"""파이 영상 수신 — QThread.

두 가지 링크가 있다. 어느 쪽이든 화면이 보는 시그널은 같다 (frame_ready · connected · failed).

    RtpLinkWorker   UDP — MAVLink 로 파이에 스트리밍을 요청하고 RTP/JPEG 로 받는다 (기본)
                    파이가 중계하는 FC 텔레메트리(MAVLink)는 계기판의 공유 상태에 넣는다
    LinkWorker      TCP — 길이 4바이트 + JPEG (옛 방식, `AEROVER_LINK=tcp`)
    LinkHub         워커의 유일한 소유자. 페이지들은 허브의 시그널을 구독만 한다

JPEG 는 `QImage.loadFromData` 로 읽는다 (opencv 불필요).
"""
from __future__ import annotations

import contextlib
import select
import socket
import threading
import time
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage

from src.config import LINK_MODE, PI_HOST, PI_PORT, RTP_PORT
from src.core import telemetry
from src.core.framing import FrameReader
from src.core.gcs import CMD_VIDEO_START, RESULT_ACCEPTED, RESULT_TEXT, GcsEndpoint
from src.core.rtpjpeg import RtpJpegDepacketizer
from src.core.telemetry.mavlink import apply_mavlink

CONNECT_TIMEOUT = 3.0
RECV_TIMEOUT = 1.0          # 1초마다 깨어나 중단 여부를 확인한다


def decode_jpeg(data: bytes) -> QImage | None:
    image = QImage()
    return image if image.loadFromData(data, "JPG") else None


def udp_sendto(sock: socket.socket, data: bytes, addr: tuple[str, int]) -> None:
    """UDP 는 보내고 잊는다. 상대가 없어 ICMP 도달 불가가 돌아와도(윈도우는 예외로 올린다) 무시한다 —
    파이가 있는지는 HEARTBEAT 로 판단한다."""
    with contextlib.suppress(OSError):
        sock.sendto(data, addr)


# ---- UDP: MAVLink + RTP ----

class RtpLinkWorker(QThread):
    """MAVLink 로 파이에 붙고 RTP/JPEG 영상을 받는 스레드.

    붙는 순서: HEARTBEAT 와 VIDEO_START_STREAMING 을 보낸다 → 파이 HEARTBEAT 가 오면 `connected`
    → RTP 패킷을 프레임으로 모아 `frame_ready`. 파이 HEARTBEAT 가 끊기면 `failed`.
    끊을 때는 VIDEO_STOP_STREAMING 을 보낸다. 지상국 HEARTBEAT 가 멎으면 파이도 스스로 멈춘다.
    """

    frame_ready = Signal(QImage, bytes)
    connected = Signal()
    failed = Signal(str)

    CONNECT_TIMEOUT = 5.0       # 이 안에 파이 HEARTBEAT 가 없으면 실패
    HEARTBEAT_TIMEOUT = 5.0     # 붙은 뒤 파이 HEARTBEAT 가 이보다 오래 없으면 끊김
    RESEND_SEC = 1.0            # ACK 가 올 때까지 시작 명령을 다시 보내는 간격
    POLL_SEC = 0.2
    RECV_BUF = 4 * 1024 * 1024

    def __init__(self, host: str = PI_HOST, port: int = PI_PORT, rtp_port: int = RTP_PORT,
                 parent=None) -> None:
        super().__init__(parent)
        self.host, self.port, self.rtp_port = host, port, rtp_port
        self._stop = threading.Event()
        self.depacketizer = RtpJpegDepacketizer()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if self._stop.is_set():
            return
        try:
            mav_sock, rtp_sock = self._open_sockets()
        except OSError as e:
            self.failed.emit(f"링크 연결 실패 — {e}")
            return

        gcs = GcsEndpoint(lambda data: udp_sendto(mav_sock, data, (self.host, self.port)))
        try:
            self._session(gcs, mav_sock, rtp_sock)
        except Exception as e:
            self.failed.emit(f"수신 중단 — {e}")
        finally:
            with contextlib.suppress(OSError):
                gcs.stop_streaming()
            mav_sock.close()
            rtp_sock.close()

    def _open_sockets(self) -> tuple[socket.socket, socket.socket]:
        mav_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        mav_sock.bind(("", 0))
        if hasattr(socket, "SIO_UDP_CONNRESET"):        # 윈도우: ICMP 도달 불가를 recv 오류로 올리지 않는다
            mav_sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rtp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.RECV_BUF)
        try:
            rtp_sock.bind(("", self.rtp_port))
        except OSError:
            mav_sock.close()
            rtp_sock.close()
            raise
        for s in (mav_sock, rtp_sock):
            s.setblocking(False)
        return mav_sock, rtp_sock

    def _session(self, gcs: GcsEndpoint, mav_sock: socket.socket, rtp_sock: socket.socket) -> None:
        store = telemetry.store()
        started = time.monotonic()
        last_request = 0.0
        joined = False

        gcs.heartbeat()
        while not self._stop.is_set():
            readable, _, _ = select.select([mav_sock, rtp_sock], [], [], self.POLL_SEC)
            if rtp_sock in readable:
                self._drain_rtp(rtp_sock)
            if mav_sock in readable:
                for msg in self._drain_mavlink(mav_sock, gcs):
                    apply_mavlink(store, msg)

            now = time.monotonic()
            result = gcs.ack_result(CMD_VIDEO_START)
            if result is not None and result != RESULT_ACCEPTED:
                why = gcs.last_error_text or RESULT_TEXT.get(result, str(result))
                self.failed.emit(f"파이가 스트리밍을 거부했다 — {why}")
                return

            if not joined:
                if gcs.camera_seen:
                    joined = True
                    self.connected.emit()
                elif now - started > self.CONNECT_TIMEOUT:
                    self.failed.emit(f"파이 응답 없음 — {self.host}:{self.port} (MAVLink)")
                    return
            elif not gcs.camera_alive(self.HEARTBEAT_TIMEOUT):
                self.failed.emit("링크 끊김 — 파이 HEARTBEAT 가 없다")
                return

            if result is None and now - last_request >= self.RESEND_SEC:
                gcs.start_streaming()                   # ACK 가 올 때까지 되풀이한다 (UDP)
                last_request = now
            gcs.tick()

    def _drain_rtp(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                packet = sock.recv(65535)
            except BlockingIOError:
                return
            jpeg = self.depacketizer.push(packet)
            if jpeg is None:
                continue
            image = decode_jpeg(jpeg)
            if image is not None:
                self.frame_ready.emit(image, jpeg)

    @staticmethod
    def _drain_mavlink(sock: socket.socket, gcs: GcsEndpoint) -> list:
        msgs: list = []
        while True:
            try:
                data = sock.recv(4096)
            except BlockingIOError:
                return msgs
            except OSError:                             # ICMP 도달 불가 (파이가 안 떠 있다)
                return msgs
            msgs += gcs.receive(data)


# ---- TCP: 옛 방식 ----

class LinkWorker(QThread):
    # 프리뷰용 QImage 와 저장용 원본 JPEG 를 함께 넘긴다.
    # 저장 시 재인코딩하지 않으려면 원본 바이트가 필요하다.
    frame_ready = Signal(QImage, bytes)
    connected = Signal()
    failed = Signal(str)

    def __init__(self, host: str = PI_HOST, port: int = PI_PORT, parent=None) -> None:
        super().__init__(parent)
        self.host = host
        self.port = port
        # 플래그가 아니라 이벤트다 — run() 이 시작되기 전에 stop() 이 와도 잃지 않는다.
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if self._stop.is_set():
            return
        try:
            conn = self._connect()
        except OSError as e:
            self.failed.emit(f"링크 연결 실패 — {e}")
            return

        self.connected.emit()
        try:
            self._pump(conn)
        except Exception as e:
            self.failed.emit(f"수신 중단 — {e}")
        finally:
            with contextlib.suppress(OSError):
                conn.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                conn.close()

    def _connect(self) -> socket.socket:
        conn = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
        conn.settimeout(RECV_TIMEOUT)
        return conn

    def _pump(self, conn: socket.socket) -> None:
        reader = FrameReader(lambda n: self._recv(conn, n))
        while not self._stop.is_set():
            data = reader.read()
            if data is None:
                return
            image = decode_jpeg(data)
            if image is None:
                continue                # 깨진 프레임은 버리고 계속 받는다
            self.frame_ready.emit(image, data)

    def _recv(self, conn: socket.socket, n: int) -> bytes | None:
        """최대 n 바이트. 타임아웃이면 중단 여부만 보고 계속 기다린다. 닫히면 None."""
        while not self._stop.is_set():
            try:
                return conn.recv(n)
            except socket.timeout:
                continue
            except OSError:
                return None
        return None


# ---- 소유자 ----

WorkerFactory = Callable[[str, int], QThread]


def default_worker(host: str, port: int) -> QThread:
    """설정(`AEROVER_LINK`)에 따라 UDP 워커나 TCP 워커를 만든다."""
    if LINK_MODE == "tcp":
        return LinkWorker(host, port)
    return RtpLinkWorker(host, port)


class LinkHub(QObject):
    """링크의 **유일한 소유자.** 앱 셸(`MainWindow`)이 하나만 들고 있다.

    파이는 **한 번에 한 지상국만 상대한다** (TCP 는 listen(1), UDP 는 마지막 HEARTBEAT 를 보낸
    주소로만 보낸다). 페이지마다 워커를 띄우면 서로 영상을 빼앗는다.

    그래서 워커는 여기 하나뿐이고, 페이지들은 신호를 **구독만** 한다.
    연결을 걸고 끊는 것은 드론 상태 페이지 한 곳이다 — 연결 지점이 둘이면
    지금 누가 소켓을 쥐고 있는지 화면에서 알 수 없어진다.
    """

    frame_ready = Signal(QImage, bytes)
    connected = Signal()
    failed = Signal(str)
    closed = Signal()           # 워커가 끝났다 (해제·실패·앱 종료 모두)

    def __init__(self, parent=None, worker_factory: WorkerFactory = default_worker) -> None:
        super().__init__(parent)
        self._make_worker = worker_factory
        self._worker: QThread | None = None

    @property
    def active(self) -> bool:
        return self._worker is not None

    def connect_to(self, host: str, port: int) -> None:
        """이미 붙어 있으면 아무것도 하지 않는다."""
        if self._worker is not None:
            return
        worker = self._make_worker(host, port)
        worker.frame_ready.connect(self.frame_ready)
        worker.connected.connect(self.connected)
        worker.failed.connect(self.failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def disconnect_from(self, wait_ms: int = 2000) -> None:
        """스레드가 실제로 끝날 때까지 기다린다. 앱 종료 경로에서도 이걸 쓴다."""
        worker = self._worker
        if worker is None:
            return
        worker.stop()
        worker.wait(wait_ms)

    def _on_finished(self) -> None:
        self._worker = None
        self.closed.emit()
