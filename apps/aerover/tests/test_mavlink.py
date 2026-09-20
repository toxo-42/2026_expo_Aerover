import pytest

pytest.importorskip("pymavlink")
from pymavlink.dialects.v20 import common as mav    # noqa: E402

from src.core.gcs import (CAMERA_COMPONENT_ID, CMD_VIDEO_START, CMD_VIDEO_STOP,   # noqa: E402
                          GcsEndpoint)
from src.core.telemetry.mavlink import apply_mavlink   # noqa: E402
from src.core.telemetry.state import TelemetryStore    # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _camera_link() -> mav.MAVLink:
    link = mav.MAVLink(None, srcSystem=1, srcComponent=CAMERA_COMPONENT_ID)
    link.robust_parsing = True
    return link


def test_gcs_sends_heartbeat_and_commands():
    sent: list[bytes] = []
    gcs = GcsEndpoint(sent.append, clock=Clock())
    gcs.heartbeat()
    gcs.start_streaming()
    gcs.stop_streaming()

    parser = _camera_link()
    msgs = [m for data in sent for m in parser.parse_buffer(data)]
    assert [m.get_type() for m in msgs] == ["HEARTBEAT", "COMMAND_LONG", "COMMAND_LONG"]
    assert msgs[0].get_srcSystem() == 255 and msgs[0].type == mav.MAV_TYPE_GCS
    assert (msgs[1].command, msgs[1].target_component, msgs[1].param1) == (CMD_VIDEO_START, CAMERA_COMPONENT_ID, 1.0)
    assert msgs[2].command == CMD_VIDEO_STOP


def test_tick_sends_heartbeat_once_per_second():
    sent, clock = [], Clock()
    gcs = GcsEndpoint(sent.append, clock=clock)
    gcs.tick(); gcs.tick()
    clock.t += 0.5
    gcs.tick()
    clock.t += 0.6
    gcs.tick()
    assert len(sent) == 2


def test_receive_tracks_camera_heartbeat_ack_and_statustext():
    clock = Clock()
    gcs = GcsEndpoint(lambda b: None, clock=clock)
    cam = _camera_link()

    assert not gcs.camera_seen
    hb = cam.heartbeat_encode(mav.MAV_TYPE_CAMERA, mav.MAV_AUTOPILOT_INVALID, 0, 0, mav.MAV_STATE_ACTIVE).pack(cam)
    ack = cam.command_ack_encode(CMD_VIDEO_START, mav.MAV_RESULT_FAILED).pack(cam)
    txt = cam.statustext_encode(mav.MAV_SEVERITY_ERROR, b"camera busy").pack(cam)
    msgs = gcs.receive(hb + ack + txt)

    assert [m.get_type() for m in msgs] == ["HEARTBEAT", "COMMAND_ACK", "STATUSTEXT"]
    assert gcs.camera_seen and gcs.camera_alive(1.0)
    assert gcs.ack_result(CMD_VIDEO_START) == mav.MAV_RESULT_FAILED
    assert gcs.last_error_text == "camera busy"
    clock.t += 2.0
    assert not gcs.camera_alive(1.0)


def test_heartbeat_from_other_components_is_not_the_camera():
    gcs = GcsEndpoint(lambda b: None, clock=Clock())
    fc = mav.MAVLink(None, srcSystem=1, srcComponent=1)
    gcs.receive(fc.heartbeat_encode(mav.MAV_TYPE_QUADROTOR, 3, 0, 0, 4).pack(fc))
    assert not gcs.camera_seen


def test_apply_mavlink_maps_fc_messages_to_store_keys():
    store = TelemetryStore(clock=lambda: 5.0)
    gps = mav.MAVLink_gps_raw_int_message(0, 3, 375665000, 1269780000, 52400, 0, 0, 342, 27050, 9)
    att = mav.MAVLink_attitude_message(0, 0.1, -0.2, 3.0, 0, 0, 0)
    sysst = mav.MAVLink_sys_status_message(0, 0, 0, 0, 16800, 1250, 77, 0, 0, 0, 0, 0, 0)
    batt = mav.MAVLink_battery_status_message(0, 0, 0, 0, [16800] + [0xFFFF] * 9, 1250, 431, -1, 76)
    hud = mav.MAVLink_vfr_hud_message(3.4, 3.5, 270, 40, 15.2, -0.3)

    assert apply_mavlink(store, gps) == ("gps",)
    assert apply_mavlink(store, att) == ("attitude",)
    assert apply_mavlink(store, sysst) == ("battery",)
    assert apply_mavlink(store, batt) == ("battery",)
    assert apply_mavlink(store, hud) == ("baro", "vario")
    assert apply_mavlink(store, mav.MAVLink_ping_message(0, 0, 0, 0)) == ()

    s = store.snapshot()
    assert abs(s["gps"]["lat"] - 37.5665) < 1e-6 and s["gps"]["sats"] == 9
    assert s["gps"]["alt_m"] == 52 and abs(s["gps"]["speed_kmh"] - 12.312) < 1e-6
    assert abs(s["gps"]["heading"] - 270.5) < 1e-6
    assert abs(s["attitude"]["pitch"] + 11.459) < 0.01 and 171 < s["attitude"]["yaw"] < 172
    assert s["battery"] == {"voltage": 16.8, "current": 12.5, "used_mah": 431, "remaining_pct": 76}
    assert s["baro"]["alt_m"] == 15.2 and abs(s["vario"]["vspeed_ms"] + 0.3) < 1e-6
    assert all(s["_rx_at"][k] == 5.0 for k in ("gps", "attitude", "battery", "baro", "vario"))


def test_unknown_values_are_left_alone():
    store = TelemetryStore()
    store.update("battery", {"voltage": 12.0, "remaining_pct": 50})
    apply_mavlink(store, mav.MAVLink_sys_status_message(0, 0, 0, 0, 0xFFFF, -1, -1, 0, 0, 0, 0, 0, 0))
    b = store.snapshot()["battery"]
    assert b["voltage"] == 12.0 and b["remaining_pct"] == 50


def _received(link: mav.MAVLink, data: bytes):
    """보낸 바이트를 다시 파싱한다 — srcComponent 가 붙은 상태로 받아야 한다."""
    parser = mav.MAVLink(None)
    parser.robust_parsing = True
    return parser.parse_buffer(data)[0]


def _fc_heartbeat(base_mode: int):
    """FC(컴포넌트 1)가 보낸 HEARTBEAT. custom_mode 22 는 2026-09-20 INAV 실측값."""
    fc = mav.MAVLink(None, srcSystem=1, srcComponent=1)
    return _received(fc, fc.heartbeat_encode(mav.MAV_TYPE_QUADROTOR, mav.MAV_AUTOPILOT_GENERIC,
                                             base_mode, 22, mav.MAV_STATE_STANDBY).pack(fc))


def test_heartbeat_sets_mode_from_arming_flag():
    store = TelemetryStore(clock=lambda: 5.0)

    assert apply_mavlink(store, _fc_heartbeat(81)) == ("mode",)      # 실측 disarmed
    assert store.snapshot()["mode"]["text"] == "DISARMED"

    assert apply_mavlink(store, _fc_heartbeat(81 | 0x80)) == ("mode",)
    s = store.snapshot()
    assert s["mode"]["text"] == "ARMED" and s["_rx_at"]["mode"] == 5.0


def test_camera_heartbeat_does_not_touch_mode():
    """파이 카메라 노드의 HEARTBEAT 가 FC 모드를 덮으면 안 된다."""
    store = TelemetryStore()
    apply_mavlink(store, _fc_heartbeat(81))
    cam = _camera_link()
    msg = _received(cam, cam.heartbeat_encode(mav.MAV_TYPE_CAMERA, mav.MAV_AUTOPILOT_INVALID,
                                              0, 0, mav.MAV_STATE_ACTIVE).pack(cam))

    assert apply_mavlink(store, msg) == ()
    assert store.snapshot()["mode"]["text"] == "DISARMED"


def _rc(rssi: int):
    fc = mav.MAVLink(None, srcSystem=1, srcComponent=1)
    ch = [1500] * 18
    return _received(fc, fc.rc_channels_encode(0, 18, *ch, rssi).pack(fc))


def test_rc_channels_rssi_fills_link_lq():
    """0~254 를 계기판이 쓰는 0~100 으로. 254 는 2026-09-20 실측 최대값."""
    store = TelemetryStore(clock=lambda: 5.0)

    assert apply_mavlink(store, _rc(254)) == ("link",)
    assert store.snapshot()["link"]["up_lq"] == 100

    apply_mavlink(store, _rc(127))
    s = store.snapshot()
    assert s["link"]["up_lq"] == 50 and s["_rx_at"]["link"] == 5.0


def test_unknown_rssi_leaves_link_untouched():
    """255 는 '모름' — 갱신하면 화면이 링크 0 을 링크 끊김으로 읽는다."""
    store = TelemetryStore()
    assert apply_mavlink(store, _rc(255)) == ()
    assert store.snapshot()["_rx_at"]["link"] is None


def test_rssi_does_not_invent_dbm():
    """MAVLink 에 없는 dBm 은 건드리지 않는다."""
    store = TelemetryStore()
    apply_mavlink(store, _rc(254))
    assert store.snapshot()["link"]["up_rssi1"] == 0
