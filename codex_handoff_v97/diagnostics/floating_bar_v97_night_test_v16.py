#!/usr/bin/env python3
"""Nine-hour diagnostic supervisor v16 (manual v97 restart; no automatic controller restoration) (Esc abort, guardian ZIP guard, complete duplicate-runner preflight) for MainStage/JamZone Floating Bar v97.

This script never edits the original v97 file. It creates a separate diagnostic
copy with UI heartbeat, click-event logging, and a test-only virtual MIDI input.
It records system/resource data, exercises known mappings, and restarts a hung
or exited diagnostic process. Use only on the user's Mac with the approved setup.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
import random
import signal
import subprocess
import sys
import shlex
import threading
import time
import traceback
from pathlib import Path

DURATION_SECONDS = 9 * 60 * 60
MIN_ACTION_GAP = 60
MAX_ACTION_GAP = 150
MAX_SUPERVISOR_RESTARTS = 12
HEARTBEAT_STALE_SECONDS = 15
INTERNAL_HEARTBEAT_WARNING_SECONDS = 5
TEST_INPUT_NAME = "Floating Bar Night Test Input"
EXPECTED_V97_SHA256 = "f1c20f91ae5442f6981cbee750979cc958b1f50944d68801b7e8517e6e9d5a3e"

HOME = Path.home()
SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE = SCRIPT_DIR / "mainstage_jamzone_fade_controller_FLOATING_BAR_v97.py"
RUN_START_EPOCH = time.time()
RUN_ID = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
OUTDIR = HOME / "Desktop" / f"floating_bar_night_test_{RUN_ID}"
OUTDIR.mkdir(parents=True, exist_ok=False)
HEARTBEAT = OUTDIR / "ui_heartbeat.json"
CLICK_LOG = OUTDIR / "mouse_events.jsonl"
MIDI_LOG = OUTDIR / "midi_events.jsonl"
BUS2_LOG = OUTDIR / "bus2_events.jsonl"
GEOMETRY = OUTDIR / "widget_geometry.json"
FREEZE_REQUEST = OUTDIR / "request_ui_freeze.json"
TIMER_SETUP_REQUEST = OUTDIR / "timer_setup_request.json"
TIMER_SETUP_RESULT = OUTDIR / "timer_setup_result.json"
CHILD_LOG = OUTDIR / "floating_bar_stdout_stderr.log"
SYSTEM_LOG = OUTDIR / "system_samples.jsonl"
ACTIONS_LOG = OUTDIR / "test_actions.jsonl"
CRASH_LOG = OUTDIR / "controller_diagnostics" / "crash.log"
CRASH_LOG_START_SIZE = 0
RUNTIME_DIR = OUTDIR / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=False)
DIAG_COPY = RUNTIME_DIR / "mainstage_jamzone_fade_controller_FLOATING_BAR_v97_NIGHTTEST.py"
RUN_STATE = OUTDIR / "run_state.json"
RUNNER_HEARTBEAT = OUTDIR / "runner_heartbeat.json"
GUARDIAN_SCRIPT = RUNTIME_DIR / "floating_bar_night_guardian.py"
GUARDIAN_LOG = OUTDIR / "guardian.log"
RUN_STATE_DATA = {
    "parent_pid": os.getpid(), "swap_started": False, "live_test_started": False,
    "diagnostic_pid": None, "active_stress_pids": [], "jamzone_restore_needed": False,
    "loop_restore_needed": False, "loop_action_log_start": None,
    "terminal_cleanup_complete": False, "normal_complete": False,
    "guardian_recovery_attempted": False, "run_outcome": "preflight"
}
GUARDIAN_PROCESS = None
GUARDIAN_RESTARTS = [0]
MAX_GUARDIAN_RESTARTS = 3

lock = threading.Lock()
ACTIVE_CHILD = None
ACTIVE_STRESS = []
RESTORE_ORIGINAL_ON_EXIT = False  # v16: original v97 is retained for manual relaunch; no automatic relaunch
SOURCE_HASH_START = [None]
EXECUTED_ACTION_COUNTS = {}
COYOTE_LAST_PRESENT = [None]
ACTION_ERROR_COUNT = [0]
LIVE_TEST_STARTED = [False]
CONTROLLER_SWAP_STARTED = [False]
ESC_ABORT_REQUESTED = [False]


def redact_value(value):
    if isinstance(value, str):
        value = value.replace(str(HOME), "~")
        value = value.replace(str(SCRIPT_DIR), ".")
        return value
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, tuple):
        return [redact_value(v) for v in value]
    return value


def sanitize_generated_text_files():
    """Redact local account/project paths from test-generated text logs only."""
    for path in OUTDIR.rglob("*"):
        if not path.is_file() or RUNTIME_DIR in path.parents:
            continue
        if path.name == "test_message_settings.json" or path.suffix.lower() not in {".log", ".json", ".jsonl", ".txt"}:
            continue
        try:
            data = path.read_text(encoding="utf-8", errors="replace")
            safe = data.replace(str(HOME), "~").replace(str(SCRIPT_DIR), ".")
            for _old, _new in (("AIRSTEP Bluetooth", "AIRSTEP"), ("SMC-Mixer Bluetooth", "SMC"),
                               ("IAC Driver Bus 1", "IAC Bus 1"), ("IAC Driver Bus 2", "IAC Bus 2")):
                safe = safe.replace(_old, _new)
            if safe != data:
                path.write_text(safe, encoding="utf-8")
        except Exception:
            pass


def log_json(path: Path, payload: dict):
    payload = redact_value({"time": dt.datetime.now().isoformat(timespec="seconds"), **payload})
    with lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            f.flush()


def note(message: str):
    message = redact_value(message)
    print(f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)
    log_json(ACTIONS_LOG, {"type": "runner", "message": message})


def minimal_environment():
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "HOME": str(HOME), "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "LANG": os.environ.get("LANG", "en_US.UTF-8")}


def run_capture(args, timeout=10):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False,
                           env=minimal_environment())
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as exc:
        return 999, "", f"{type(exc).__name__}: {exc}"


def write_json_atomic(path: Path, payload: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def write_runner_heartbeat(phase="running"):
    """Local liveness marker; the independent guardian can recover if this runner itself stalls."""
    try:
        write_json_atomic(RUNNER_HEARTBEAT, {"pid": os.getpid(), "monotonic": time.monotonic(),
                                             "epoch": time.time(), "phase": phase})
    except Exception:
        pass


def update_run_state(**updates):
    RUN_STATE_DATA.update(updates)
    write_json_atomic(RUN_STATE, RUN_STATE_DATA)


def get_process_rows():
    rc, out, err = run_capture(["ps", "-axo", "pid=,ppid=,%cpu=,rss=,etime=,stat=,command="], timeout=10)
    rows = []
    if rc == 0:
        lines = out.splitlines()
        for line in lines:
            parts = line.strip().split(None, 6)
            if len(parts) == 7:
                try:
                    rows.append({"pid": int(parts[0]), "ppid": int(parts[1]), "cpu_pct": float(parts[2]),
                                 "rss_kb": int(parts[3]), "elapsed": parts[4], "state": parts[5], "command": parts[6]})
                except ValueError:
                    pass
    return rows


def executed_script_basename(command):
    """Return the first Python script actually executed, not a later path argument."""
    try:
        tokens = shlex.split(str(command or ""))
    except Exception:
        tokens = str(command or "").split()
    for token in tokens[1:]:
        if token.lower().endswith(".py"):
            return os.path.basename(token)
    return None


def is_controller_row(row, version=None):
    script = executed_script_basename(row.get("command", ""))
    valid = {
        "v96": {"mainstage_jamzone_fade_controller_FLOATING_BAR_v96.py"},
        "v97": {"mainstage_jamzone_fade_controller_FLOATING_BAR_v97.py"},
        "diagnostic": {"mainstage_jamzone_fade_controller_FLOATING_BAR_v97_NIGHTTEST.py",
                       "mainstage_jamzone_fade_controller_FLOATING_BAR_v96_NIGHTTEST.py"},
    }
    if version:
        return script in valid.get(version, set())
    return any(script in values for values in valid.values())


def process_label(command):
    """Return a safe process label without command arguments or filesystem paths."""
    raw = str(command or "")
    low = raw.lower()
    script = executed_script_basename(raw)
    exact_labels = {
        "mainstage_jamzone_fade_controller_FLOATING_BAR_v97.py": "Floating Bar v97",
        "mainstage_jamzone_fade_controller_FLOATING_BAR_v96.py": "Floating Bar v96",
        "mainstage_jamzone_fade_controller_FLOATING_BAR_v97_NIGHTTEST.py": "Floating Bar diagnostic",
        "mainstage_jamzone_fade_controller_FLOATING_BAR_v96_NIGHTTEST.py": "Floating Bar diagnostic",
        "floating_bar_v97_night_test_v16.py": "Night-test runner",
        "floating_bar_night_guardian.py": "Night-test guardian",
    }
    if script in exact_labels:
        return exact_labels[script]
    known = [
        ("mainstage_jamzone_fade_controller_floatING_bar_v97_nighttest.py".lower(), "Floating Bar diagnostic"),
        ("mainstage_jamzone_fade_controller_floatING_bar_v97.py".lower(), "Floating Bar v97"),
        ("mainstage_jamzone_fade_controller_floatING_bar_v96_nighttest.py".lower(), "Floating Bar v96 diagnostic"),
        ("mainstage_jamzone_fade_controller_floatING_bar_v96.py".lower(), "Floating Bar v96"),
        ("mainstage", "MainStage"), ("jamzone", "JamZone"), ("songbookpro", "SongBook Pro"),
        ("songbook pro", "SongBook Pro"), ("google chrome helper", "Google Chrome Helper"),
        ("google chrome", "Google Chrome"), ("spotify", "Spotify"), ("midiserver", "MIDIServer"),
        ("windowserver", "WindowServer"), ("coreaudiod", "coreaudiod"),
        ("coyotemidi", "CoyoteMIDI"), ("octa-capture", "OCTA-CAPTURE"),
        ("python", "Python"), ("osascript", "osascript"),
    ]
    for marker, label in known:
        if marker in low:
            return label
    first = raw.strip().split(None, 1)[0] if raw.strip() else "unknown"
    return os.path.basename(first)[:64] or "unknown"


def safe_process_row(row):
    return {"pid": row.get("pid"), "ppid": row.get("ppid"),
            "cpu_pct": row.get("cpu_pct"), "rss_kb": row.get("rss_kb"),
            "elapsed": row.get("elapsed"), "state": row.get("state"),
            "process_name": process_label(row.get("command", ""))}


def get_controller_pids():
    rows = get_process_rows()
    return sorted({r["pid"] for r in rows if is_controller_row(r)})


def make_diagnostic_copy():
    if not SOURCE.is_file():
        raise RuntimeError(f"Original v97 file not found next to this test runner: {SOURCE}")
    src = SOURCE.read_text(encoding="utf-8")
    if "Integrated v97" not in src:
        raise RuntimeError("The controller source is not marked Integrated v97; refusing to proceed.")
    import hashlib
    SOURCE_HASH_START[0] = hashlib.sha256(src.encode("utf-8")).hexdigest()
    if SOURCE_HASH_START[0] != EXPECTED_V97_SHA256:
        raise RuntimeError(f"v97 source hash mismatch: expected {EXPECTED_V97_SHA256}, got {SOURCE_HASH_START[0]}; refusing to stop any running controller.")
    if "def _install_night_test_instrumentation(root, controller):" in src:
        raise RuntimeError("Source already contains night-test instrumentation; refusing to patch unexpectedly.")
    if DIAG_COPY.exists():
        raise RuntimeError("Unique diagnostic-copy destination already exists; refusing to overwrite any file.")

    crash_path_old = """        log_dir = os.path.expanduser(
            '~/Library/Logs/MainStageJamZoneController'
        )"""
    crash_path_new = """        log_dir = os.path.join(os.environ.get('FLOATING_BAR_NIGHT_TEST_DIR', os.path.expanduser('~/Desktop')), 'controller_diagnostics')"""
    if src.count(crash_path_old) != 1:
        raise RuntimeError("Could not uniquely redirect controller crash diagnostics to the test-only folder; refusing to touch the user's Library logs.")
    src = src.replace(crash_path_old, crash_path_new, 1)
    settings_path_old = """        self.message_settings_path = os.path.join(
            os.path.expanduser('~/Library/Application Support/MainStageJamZoneController'),
            'message_settings.json'
        )"""
    settings_path_new = """        self.message_settings_path = os.path.join(os.environ.get('FLOATING_BAR_NIGHT_TEST_DIR', os.path.expanduser('~/Desktop')), 'test_message_settings.json')"""
    if src.count(settings_path_old) != 1:
        raise RuntimeError("Could not uniquely redirect message settings to the test-only folder; refusing to read or modify personal settings.")
    src = src.replace(settings_path_old, settings_path_new, 1)

    instrumentation = '''

def _install_night_test_instrumentation(root, controller):
    """Diagnostic-only instrumentation; does not alter control mappings."""
    import json as _json, os as _os, time as _time, threading as _threading
    _base = _os.environ.get("FLOATING_BAR_NIGHT_TEST_DIR", _os.path.expanduser("~/Desktop"))
    _os.makedirs(_base, exist_ok=True)
    _heartbeat_path = _os.path.join(_base, "ui_heartbeat.json")
    _history_path = _os.path.join(_base, "heartbeat_history.jsonl")
    _geometry_path = _os.path.join(_base, "widget_geometry.json")
    _click_path = _os.path.join(_base, "mouse_events.jsonl")
    _midi_path = _os.path.join(_base, "midi_events.jsonl")
    _bus2_path = _os.path.join(_base, "bus2_events.jsonl")
    _lock = _threading.Lock()
    _midi_counts = {"total": 0, "cc11": 0, "last_cc11_log": 0.0}
    _callback_source = _threading.local()
    _last_due = [_time.monotonic()]
    _last_history_write = [0.0]
    _freeze_ack_path = _os.path.join(_base, "freeze_request_ack.json")
    _last_geometry_write = [0.0]
    _click_count = [0]
    _last_click = [None]
    def _atomic_json(path, payload):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as _f:
            _json.dump(payload, _f); _f.flush(); os.fsync(_f.fileno())
        _os.replace(tmp, path)
    def _beat():
        # A one-time, externally requested UI-thread stall validates the external supervisor.
        # This code exists only in the diagnostic copy and is never inserted into original v97.
        _freeze_request = _os.path.join(_base, "request_ui_freeze.json")
        try:
            if _os.path.isfile(_freeze_request):
                with open(_freeze_request, "r", encoding="utf-8") as _ff:
                    _freeze_req = _json.load(_ff)
                _freeze_req_id = _freeze_req.get("request_id")
                try:
                    with open(_freeze_ack_path, "r", encoding="utf-8") as _af: _freeze_ack = _json.load(_af)
                except Exception: _freeze_ack = {}
                if _freeze_req_id and _freeze_req_id != _freeze_ack.get("request_id"):
                    _freeze_seconds = max(8, min(30, int(_freeze_req.get("seconds", 10))))
                    _atomic_json(_freeze_ack_path, {"request_id": _freeze_req_id, "status": "started"})
                    print(f"[NIGHT TEST] Intentional diagnostic-only UI freeze for {_freeze_seconds}s to test external watchdog.", flush=True)
                    _time.sleep(_freeze_seconds)
        except Exception as _freeze_exc:
            print(f"[NIGHT TEST] UI freeze probe request error: {_freeze_exc}", flush=True)
        timer_setup_result = None
        try:
            if _os.path.isfile(_os.path.join(_base, "timer_setup_request.json")):
                _req_path = _os.path.join(_base, "timer_setup_request.json")
                _result_path = _os.path.join(_base, "timer_setup_result.json")
                with open(_req_path, "r", encoding="utf-8") as _rf: _req = _json.load(_rf)
                try:
                    with open(_result_path, "r", encoding="utf-8") as _rr: _previous_result = _json.load(_rr)
                except Exception: _previous_result = {}
                _req_id = _req.get("request_id")
                if _req_id and (_previous_result.get("request_id") != _req_id or _previous_result.get("status") == "pending"):
                    _bar_for_timer = getattr(controller, "floating_bar", None)
                    _seconds = int(_req.get("seconds", 0))
                    if _bar_for_timer is None or _seconds < 60 or _seconds > 12 * 3600:
                        timer_setup_result = {"request_id": _req_id, "status": "rejected", "seconds": _seconds}
                    else:
                        _bar_for_timer.set_timer_seconds = _seconds
                        _bar_for_timer.session_remaining = _seconds
                        _bar_for_timer.session_deadline = None
                        _bar_for_timer.session_expired = False
                        _bar_for_timer._timer_flash_active = False
                        _bar_for_timer._timer_flash_on = False
                        try: _bar_for_timer._paint_tree(_bar_for_timer.session_tile, "#393326")
                        except Exception: pass
                        _bar_for_timer.session_timer_label.configure(bg="#393326", fg=_bar_for_timer.YELLOW,
                                                                      text=_bar_for_timer._format_hms(_seconds))
                        timer_setup_result = {"request_id": _req_id, "status": "applied",
                                              "seconds": _seconds,
                                              "saved_default_seconds": getattr(_bar_for_timer, "default_timer_seconds", None)}
                    _atomic_json(_result_path, timer_setup_result)
                    print(f"[NIGHT TEST] Timer setup request result: {timer_setup_result}", flush=True)
        except Exception as _timer_setup_exc:
            timer_setup_result = {"status": "error", "error": f"{type(_timer_setup_exc).__name__}: {_timer_setup_exc}"}
            try: _atomic_json(_os.path.join(_base, "timer_setup_result.json"), timer_setup_result)
            except Exception: pass
        now = _time.monotonic()
        lag = max(0.0, now - _last_due[0])
        _last_due[0] = now + 1.0
        bar = getattr(controller, "floating_bar", None)
        editor_open = False
        context_menu_mapped = False
        visible = None
        geometry = {}
        if bar is not None:
            try:
                editor = getattr(bar, "_timer_editor", None)
                editor_open = bool(editor is not None and editor.winfo_exists() and editor.winfo_viewable())
            except Exception: pass
            try: context_menu_mapped = bool(bar.context_menu.winfo_ismapped())
            except Exception: pass
            try: visible = bool(bar.visible)
            except Exception: pass
            names = ("session_tile", "clock_label", "session_timer_label", "timer_toggle_button",
                     "timer_reset_button", "bpm_tile", "midi_tile")
            for name in names:
                try:
                    w = getattr(bar, name)
                    if w.winfo_exists():
                        geometry[name] = {"root_x": w.winfo_rootx(), "root_y": w.winfo_rooty(),
                                          "width": w.winfo_width(), "height": w.winfo_height(),
                                          "class": w.winfo_class(), "text": str(w.cget("text"))[:80] if "text" in w.keys() else None,
                                          "background": (str(w.cget("bg")) if "bg" in w.keys() else None),
                                          "foreground": (str(w.cget("fg")) if "fg" in w.keys() else None)}
                except Exception: pass
            for name, w in getattr(bar, "status_tiles", {}).items():
                try:
                    geometry["status_tile:" + name] = {"root_x": w.winfo_rootx(), "root_y": w.winfo_rooty(),
                        "width": w.winfo_width(), "height": w.winfo_height(), "class": w.winfo_class(), "text": name}
                except Exception: pass
            for name, w in getattr(bar, "effect_tiles", {}).items():
                try:
                    geometry["effect_tile:" + name] = {"root_x": w.winfo_rootx(), "root_y": w.winfo_rooty(),
                        "width": w.winfo_width(), "height": w.winfo_height(), "class": w.winfo_class(), "text": name,
                        "background": str(w.cget("bg")) if "bg" in w.keys() else None}
                except Exception: pass
            try:
                if context_menu_mapped:
                    # Menu index 2 is the non-destructive "Show bar" command.
                    _bbox = bar.context_menu.bbox(2)
                    if _bbox:
                        _mx, _my, _mw, _mh = _bbox
                        geometry["context_menu:show_bar"] = {"root_x": bar.context_menu.winfo_rootx() + _mx,
                            "root_y": bar.context_menu.winfo_rooty() + _my, "width": _mw, "height": _mh,
                            "class": "MenuEntry", "text": "Show bar"}
            except Exception: pass
        # Capture all current widget geometry, including timer-editor Entry fields and menu-related controls.
        def _collect_geometry(w):
            try:
                geometry["widget:" + str(w)] = {"root_x": w.winfo_rootx(), "root_y": w.winfo_rooty(),
                    "width": w.winfo_width(), "height": w.winfo_height(), "class": w.winfo_class(),
                    "text": str(w.cget("text"))[:80] if "text" in w.keys() else None,
                    "background": (str(w.cget("bg")) if "bg" in w.keys() else None),
                    "foreground": (str(w.cget("fg")) if "fg" in w.keys() else None)}
            except Exception: pass
            try:
                for child in w.winfo_children(): _collect_geometry(child)
            except Exception: pass
        _collect_geometry(root)
        try:
            focused = root.focus_get()
            focused_widget = str(focused) if focused is not None else None
        except Exception: focused_widget = None
        timer_running = None
        timer_remaining = None
        timer_expired = None
        if bar is not None:
            try: timer_running = bool(bar.session_deadline is not None)
            except Exception: pass
            try: timer_remaining = round(float(bar.session_remaining), 2)
            except Exception: pass
            try: timer_expired = bool(bar.session_expired)
            except Exception: pass
        connection_state = {}
        try:
            with controller.port_status_lock:
                _port_snapshot = controller.port_status_snapshot
            _inputs = list(_port_snapshot[0]) if _port_snapshot and _port_snapshot[0] is not None else []
            _outputs = list(_port_snapshot[1]) if _port_snapshot and _port_snapshot[1] is not None else []
            _bar_for_state = getattr(controller, "floating_bar", None)
            connection_state = {
                "receiver_failed": bool(getattr(controller, "receiver_failed", False)),
                "receiver_thread_alive": bool(getattr(getattr(controller, "receiver_thread", None), "is_alive", lambda: False)()),
                "smc_receiver_thread_alive": bool(getattr(getattr(controller, "smc_receiver_thread", None), "is_alive", lambda: False)()),
                "bus2_receiver_thread_alive": bool(getattr(getattr(controller, "iac_state_receiver_thread", None), "is_alive", lambda: False)()),
                "port_failure_present": bool(str(getattr(controller, "port_failure", ""))),
                "port_snapshot_error_present": bool(_port_snapshot and _port_snapshot[2]),
                "input_port_count": len(_inputs), "output_port_count": len(_outputs),
                "airstep_present": any("AIRSTEP Bluetooth" in n for n in _inputs),
                "smc_present": any("SMC-Mixer Bluetooth" in n for n in (_inputs + _outputs)),
                "iac_bus1_present": any("IAC Driver Bus 1" in n for n in (_inputs + _outputs)),
                "iac_bus2_present": any("IAC Driver Bus 2" in n for n in _inputs),
                "battery_levels": dict(getattr(_bar_for_state, "_controller_battery_levels", {})) if _bar_for_state is not None else {},
                "connected_state": dict(getattr(_bar_for_state, "_controller_connected_state", {})) if _bar_for_state is not None else {},
                "fault_detected_at_monotonic": getattr(controller, "fault_detected_at", None),
            }
        except Exception as _conn_exc:
            connection_state = {"snapshot_error_type": type(_conn_exc).__name__}
        controller_state = {}
        try:
            with controller.lock:
                controller_state = {
                    "fade_active": bool(controller.fade.active),
                    "fade_generation": int(getattr(controller.fade, "generation", 0)),
                    "fade_current_value": getattr(controller.fade, "current_value", None),
                    "down_sequence_active": bool(getattr(controller.fade, "down_sequence_active", False)),
                    "short_action_active": bool(getattr(controller.fade, "short_action_active", False)),
                    "scenario_busy": bool(controller.scenario_busy),
                    "awaiting_tt": bool(controller.awaiting_tt),
                    "lockout_remaining_seconds": round(max(0.0, controller.lockout_until - _time.monotonic()), 3),
                    "g2m_repeat_active": bool(controller.g2m_repeat_active),
                    "tb_repeat_active": bool(controller.tb_repeat_active),
                    "tile_states": dict(controller.tile_states),
                    "fx_state": str(controller.fx_state),
                    "fx_global_muted": bool(controller.fx_global_muted),
                    "guitar_muted": bool(controller.guitar_muted),
                    "silent": bool(controller.silent),
                    "last_midi_in_display": str(controller.last_midi_in_display),
                    "last_midi_in_name_present": bool(str(controller.last_midi_in_name)),
                }
        except Exception as _state_exc:
            controller_state = {"snapshot_error_type": type(_state_exc).__name__}
        payload = {"wall_time": _time.strftime("%Y-%m-%d %H:%M:%S"), "epoch": _time.time(),
                   "monotonic": now, "ui_tick_lag_seconds": round(lag, 4), "pid": os.getpid(),
                   "click_events_seen": _click_count[0], "timer_editor_open": editor_open,
                   "context_menu_mapped": context_menu_mapped, "bar_visible": visible,
                   "timer_running": timer_running, "timer_remaining_seconds": timer_remaining,
                   "timer_expired": timer_expired, "focused_widget": focused_widget,
                   "timer_setup_result": timer_setup_result, "connection_state": connection_state,
                   "controller_state": controller_state}
        try:
            _atomic_json(_heartbeat_path, payload)
            if now - _last_history_write[0] >= 5.0:
                with open(_history_path, "a", encoding="utf-8") as _hf:
                    _hf.write(_json.dumps(payload, ensure_ascii=False) + "\\n")
                    _hf.flush()
                _last_history_write[0] = now
            if now - _last_geometry_write[0] >= 5.0:
                _atomic_json(_geometry_path, {"wall_time": payload["wall_time"], "pid": os.getpid(), "widgets": geometry})
                _last_geometry_write[0] = now
        except Exception: pass
        if lag > 0.25:
            try:
                with open(_click_path, "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps({"wall_time": payload["wall_time"], "type": "UI_TICK_LAG",
                                          "lag_seconds": payload["ui_tick_lag_seconds"], "pid": os.getpid()}) + "\\n")
            except Exception: pass
        try: root.after(1000, _beat)
        except tk.TclError: pass
    def _mouse_event(event):
        try:
            _click_count[0] += 1
            widget = event.widget
            now = _time.monotonic()
            prev = _last_click[0]
            delta = None if prev is None else round(now - prev, 4)
            if getattr(event, "type", None) == tk.EventType.ButtonPress:
                _last_click[0] = now
            payload = {"wall_time": _time.strftime("%Y-%m-%d %H:%M:%S"), "monotonic": now,
                       "event_type": str(event.type), "button": getattr(event, "num", None),
                       "widget": str(widget), "widget_class": widget.winfo_class(),
                       "widget_text": str(widget.cget("text"))[:80] if "text" in widget.keys() else None,
                       "local_x": getattr(event, "x", None), "local_y": getattr(event, "y", None),
                       "root_x": getattr(event, "x_root", None), "root_y": getattr(event, "y_root", None),
                       "widget_width": widget.winfo_width(), "widget_height": widget.winfo_height(),
                       "since_previous_press_seconds": delta, "pid": os.getpid()}
            with _lock:
                with open(_click_path, "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps(payload, ensure_ascii=False) + "\\n"); _f.flush()
        except Exception:
            pass
    _bound_widgets = set()
    def _bind_widgets(widget):
        try:
            key = str(widget)
            if key not in _bound_widgets:
                _bound_widgets.add(key)
                widget.bind("<ButtonPress-1>", _mouse_event, add="+")
                widget.bind("<ButtonRelease-1>", _mouse_event, add="+")
                widget.bind("<Double-Button-1>", _mouse_event, add="+")
                widget.bind("<ButtonPress-2>", _mouse_event, add="+")
                widget.bind("<ButtonPress-3>", _mouse_event, add="+")
                try:
                    if widget.winfo_class() == "Toplevel" and widget.title() == "Set timer for this run":
                        widget.bind("<Escape>", lambda _e, w=widget: (w.destroy(), "break")[1], add="+")
                except Exception: pass
            for child in widget.winfo_children(): _bind_widgets(child)
        except tk.TclError: pass
    def _message_fields(msg):
        fields = {"type": getattr(msg, "type", None)}
        for name in ("channel", "control", "value", "note", "velocity", "pitch", "program"):
            if hasattr(msg, name): fields[name] = getattr(msg, name)
        if "channel" in fields: fields["midi_channel_1_based"] = fields["channel"] + 1
        return fields
    def _log_midi_input(msg):
        try:
            now = _time.monotonic()
            with _lock:
                _midi_counts["total"] += 1
                if getattr(msg, "type", None) == "control_change" and getattr(msg, "control", None) == 11:
                    _midi_counts["cc11"] += 1
                    # Avoid flooding the disk with high-rate expression traffic; log every 100th packet and totals.
                    if _midi_counts["cc11"] % 100 != 0 and now - _midi_counts["last_cc11_log"] < 10:
                        return
                    _midi_counts["last_cc11_log"] = now
                    fields = {"type": "cc11_aggregate_sample", "cc11_count": _midi_counts["cc11"],
                              "total_callback_messages": _midi_counts["total"], "last_value": getattr(msg, "value", None)}
                else:
                    fields = _message_fields(msg)
                payload = {"wall_time": _time.strftime("%Y-%m-%d %H:%M:%S"), "epoch": _time.time(),
                           "monotonic": now, "source": getattr(_callback_source, "name", "AIRSTEP_callback"),
                           "pid": _os.getpid(), **fields}
                with open(_midi_path, "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps(payload, ensure_ascii=False) + "\\n"); _f.flush()
        except Exception: pass
    def _log_bus2_message(msg):
        try:
            relevant = ((getattr(msg, "type", None) == "control_change" and
                         getattr(msg, "channel", None) == 14 and getattr(msg, "control", None) in (39, 40, 41, 42, 43, 45)) or
                        (getattr(msg, "type", None) in ("note_on", "note_off") and
                         getattr(msg, "note", None) in (0, 2, 3, 11, 14, 18, 22)))
            if not relevant: return
            payload = {"wall_time": _time.strftime("%Y-%m-%d %H:%M:%S"), "epoch": _time.time(),
                       "monotonic": _time.monotonic(), "pid": _os.getpid(), **_message_fields(msg)}
            with open(_bus2_path, "a", encoding="utf-8") as _f:
                _f.write(_json.dumps(payload, ensure_ascii=False) + "\\n"); _f.flush()
        except Exception: pass
    controller._night_test_log_midi_input = _log_midi_input
    controller._night_test_log_bus2_message = _log_bus2_message
    def _test_virtual_callback(msg):
        _callback_source.name = "virtual_test_input"
        try: controller._handle_midi_input_callback(msg)
        finally: _callback_source.name = "AIRSTEP_callback"
    def _scan_widgets():
        _bind_widgets(root)
        try: root.after(2000, _scan_widgets)
        except tk.TclError: pass
    _scan_widgets()
    root.after(0, _beat)
    # Test-only virtual MIDI input routes test messages through the same handler
    # as the physical AIRSTEP. Existing AIRSTEP input/routing remains unchanged.
    try:
        controller.night_test_input = mido.open_input(
            "Floating Bar Night Test Input", callback=_test_virtual_callback, virtual=True
        )
        print("[NIGHT TEST] Test-only virtual MIDI input ready: Floating Bar Night Test Input")
    except Exception as exc:
        controller.night_test_input = None
        print(f"[NIGHT TEST] VIRTUAL MIDI INPUT FAILED: {type(exc).__name__}: {exc}")
    print(f"[NIGHT TEST] UI heartbeat and mouse-event logging enabled in {_base}")
'''
    marker = "if __name__ == \"__main__\":"
    if marker not in src:
        raise RuntimeError("Could not locate main guard in v96 source.")
    src = src.replace(marker, instrumentation + "\n\n" + marker, 1)
    midi_callback_marker = '''    def _handle_midi_input_callback(self, msg):
        """Process AIRSTEP messages delivered by the backend callback thread."""
        if self.stop_event.is_set():'''
    midi_callback_replacement = '''    def _handle_midi_input_callback(self, msg):
        """Process AIRSTEP messages delivered by the backend callback thread."""
        try:
            logger = getattr(self, "_night_test_log_midi_input", None)
            if logger is not None: logger(msg)
        except Exception: pass
        if self.stop_event.is_set():'''
    if src.count(midi_callback_marker) != 1:
        raise RuntimeError("Could not uniquely instrument the MIDI input callback.")
    src = src.replace(midi_callback_marker, midi_callback_replacement, 1)
    bus2_marker = "            for msg in self.iac_state_in_port:\n                if self.stop_event.is_set():"
    bus2_replacement = "            for msg in self.iac_state_in_port:\n                try:\n                    logger = getattr(self, '_night_test_log_bus2_message', None)\n                    if logger is not None: logger(msg)\n                except Exception: pass\n                if self.stop_event.is_set():"
    if src.count(bus2_marker) != 1:
        raise RuntimeError("Could not uniquely instrument the Bus 2 receiver.")
    src = src.replace(bus2_marker, bus2_replacement, 1)
    watchdog_marker = "        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, name='UIWatchdog', daemon=True)\n        self._watchdog_thread.start()"
    watchdog_replacement = "        if os.environ.get('FLOATING_BAR_NIGHT_TEST_DISABLE_INTERNAL_WATCHDOG') == '1':\n            print('[NIGHT TEST] Internal watchdog disabled for external-supervisor-only freeze probe.', flush=True)\n            self._watchdog_thread = None\n        else:\n            self._watchdog_thread = threading.Thread(target=self._watchdog_loop, name='UIWatchdog', daemon=True)\n            self._watchdog_thread.start()"
    if src.count(watchdog_marker) != 1:
        raise RuntimeError("Could not uniquely add the diagnostic-only watchdog test switch.")
    src = src.replace(watchdog_marker, watchdog_replacement, 1)
    call_marker = "        self.floating_bar = FloatingBar(root, self)"
    if src.count(call_marker) != 1:
        raise RuntimeError("Expected exactly one FloatingBar construction point; refusing to patch.")
    src = src.replace(call_marker, call_marker + "\n        _install_night_test_instrumentation(root, self)", 1)
    # Close the test-only virtual input in the existing cleanup path.
    cleanup_marker = "                        if self.out_port is not None:\n                            self.out_port.close()"
    if cleanup_marker in src:
        src = src.replace(cleanup_marker,
                          "                        if getattr(self, 'night_test_input', None) is not None:\n                            self.night_test_input.close()\n                        if self.out_port is not None:\n                            self.out_port.close()", 1)
    else:
        raise RuntimeError("Could not locate MIDI output cleanup path; refusing to generate a diagnostic copy without proper cleanup.")
    # Redact dynamic Bluetooth/CoreMIDI port names from diagnostic stdout while retaining readiness markers.
    for old_line, new_line in (
        ("print(f'[BT MIDI RECOVERY] Opened SMC state input: {candidate}')", "print('[BT MIDI RECOVERY] Opened SMC state input.')"),
        ("print(f'[BT MIDI RECOVERY] Could not open SMC state input: {type(exc).__name__}: {exc}; retrying.')", "print(f'[BT MIDI RECOVERY] Could not open SMC state input: {type(exc).__name__}; retrying.')"),
        ("print(f'[BT MIDI RECOVERY] Opened MIDI input: {AIRSTEP_NAME}')", "print('[BT MIDI RECOVERY] Opened MIDI input: AIRSTEP')"),
        ("print(f'[BT MIDI RECOVERY] Reopened MIDI input after error: {AIRSTEP_NAME}')", "print('[BT MIDI RECOVERY] Reopened MIDI input after error: AIRSTEP')"),
        ("print(f'MIDI diagnostic input: {iac2_candidates[0]}')", "print('MIDI diagnostic input: IAC Bus 2')"),
        ("print(f'MIDI input : {AIRSTEP_NAME} (automatic reconnect enabled)')", "print('MIDI input: AIRSTEP (automatic reconnect enabled)')"),
        ("print(f'MIDI output: {IAC_NAME}')", "print('MIDI output: IAC Bus 1')"),
        ("print('[SMC STATE] SMC-Mixer Bluetooth not present at startup; recovery will keep checking.')", "print('[SMC STATE] SMC not present at startup; recovery will keep checking.')"),
        ("print('[BT MIDI DISCONNECT] AIRSTEP Bluetooth input disappeared from CoreMIDI enumeration.')", "print('[BT MIDI DISCONNECT] AIRSTEP input disappeared from CoreMIDI enumeration.')"),
        ("print('[BT MIDI RECONNECT] AIRSTEP Bluetooth input reappeared in CoreMIDI enumeration.')", "print('[BT MIDI RECONNECT] AIRSTEP input reappeared in CoreMIDI enumeration.')"),
        ("print('[BT MIDI DISCONNECT] SMC-Mixer Bluetooth input disappeared from CoreMIDI enumeration.')", "print('[BT MIDI DISCONNECT] SMC input disappeared from CoreMIDI enumeration.')"),
        ("print('[BT MIDI RECONNECT] SMC-Mixer Bluetooth input reappeared in CoreMIDI enumeration.')", "print('[BT MIDI RECONNECT] SMC input reappeared in CoreMIDI enumeration.')"),
        ("print('[BT RECONNECT] All monitored Bluetooth controllers are present.')", "print('[BT RECONNECT] All monitored controllers are present.')"),
    ):
        if old_line in src:
            src = src.replace(old_line, new_line)
    # Remove any dynamic port lists/names from the diagnostic-only copy's status messages.
    for old_line, new_line in (
        ("f'SMC_names={smc_matches}; available_inputs={inputs}'", "f'SMC_match_count={len(smc_matches)}; available_input_count={len(inputs)}'"),
        ("missing.append('AIRSTEP Bluetooth')", "missing.append('AIRSTEP')"),
        ("missing.append('SMC-Mixer Bluetooth')", "missing.append('SMC')"),
        ("print(f'[BT MIDI MONITOR] Status logging failed: {type(exc).__name__}: {exc}')", "print(f'[BT MIDI MONITOR] Status logging failed: {type(exc).__name__}')"),
        ("print(f'[BT MIDI RECOVERY] Closing stale AIRSTEP input: {type(exc).__name__}: {exc}')", "print(f'[BT MIDI RECOVERY] Closing stale AIRSTEP input: {type(exc).__name__}')"),
        ("error_key = f'{type(exc).__name__}: {exc}'", "error_key = type(exc).__name__"),
        ("""raise RuntimeError(f"Output '{IAC_NAME}' not found. Available: {outputs}")""", "raise RuntimeError('IAC Bus 1 output not found.')"),
        ("print(f'[MIDI STATUS] {AIRSTEP_NAME} is present; receiver will open it in the recovery worker.')", "print('[MIDI STATUS] AIRSTEP is present; receiver will open it in the recovery worker.')"),
        ("print(f'[MIDI STATUS] WARNING: Input {AIRSTEP_NAME!r} not found at startup. The recovery worker will keep checking and open it when it returns.')", "print('[MIDI STATUS] WARNING: AIRSTEP input not found at startup; recovery worker will keep checking.')"),
        ("print(f'[IAC BUS2 WARNING] Input {IAC_STATE_INPUT_NAME!r} not found; available inputs: {inputs}')", "print('[IAC BUS2 WARNING] IAC Bus 2 input not found; recovery status will be logged.')"),
        ("print('[SMC STATE] SMC-Mixer Bluetooth not present at startup; recovery will keep checking.')", "print('[SMC STATE] SMC not present at startup; recovery will keep checking.')"),
        ("print(f'MIDI input : {AIRSTEP_NAME} (automatic reconnect enabled)')", "print('MIDI input: AIRSTEP (automatic reconnect enabled)')"),
        ("print(f'MIDI output: {IAC_NAME}')", "print('MIDI output: IAC Bus 1')"),
        ("print('Floating Bar listens for MainStage status mirrors on IAC Driver Bus 2, MIDI CH15. Ensure each button mapping sends its configured state CC/Note on activation and deactivation; no separate request command is sent.')", "print('Floating Bar listens for MainStage status mirrors on IAC Bus 2, MIDI CH15.')"),
    ):
        if old_line in src:
            src = src.replace(old_line, new_line)
    # The readiness parser uses these generic labels, so it never needs to log the original device names.
    # Keep the diagnostic report to port-presence booleans/counts, never raw Bluetooth/MIDI device names.
    for forbidden in ('"input_names": _inputs', '"output_names": _outputs', '"last_midi_in_name": str(controller.last_midi_in_name)'):
        if forbidden in src:
            raise RuntimeError("Privacy check failed: raw MIDI/device names would be written to diagnostics; refusing to proceed.")
    DIAG_COPY.write_text(src, encoding="utf-8")
    return DIAG_COPY


def find_midi_output():
    try:
        import mido
    except Exception as exc:
        raise RuntimeError(f"Cannot import mido: {exc}")
    names = mido.get_output_names()
    exact = next((n for n in names if n == "IAC Driver Bus 1"), None)
    if not exact:
        exact = next((n for n in names if "IAC Driver Bus 1" in n), None)
    testport = next((n for n in names if TEST_INPUT_NAME in n), None)
    return exact, testport, names


def apple_script(script, timeout=8):
    return run_capture(["osascript", "-e", script], timeout=timeout)


def jamzone_exists():
    rc, out, err = apple_script('tell application "System Events" to (name of processes) contains "Jamzone"')
    return rc == 0 and out.lower() == "true"


def activate_jamzone():
    if not jamzone_exists():
        log_json(ACTIONS_LOG, {"type": "jamzone", "result": "not-running; not launched automatically"})
        return False
    rc, out, err = apple_script('tell application "Jamzone" to activate')
    time.sleep(0.15)
    actual = frontmost_process_info()
    verified = rc == 0 and "jamzone" in str(actual.get("name") or "").lower()
    log_json(ACTIONS_LOG, {"type": "jamzone_activate", "rc": rc, "stdout": out, "stderr": err,
                           "actual_frontmost": actual, "verified": verified})
    return verified


def send_keycode(keycode: int):
    return apple_script(f'tell application "System Events" to key code {keycode}')


def locate_bar_bounds():
    try:
        import Quartz
        wins = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
        candidates = []
        for w in wins or []:
            title = str(w.get("kCGWindowName", ""))
            owner = str(w.get("kCGWindowOwnerName", ""))
            bounds = w.get("kCGWindowBounds") or {}
            if ("Integrated v97" in title or "Floating Bar" in title) and float(bounds.get("Width", 0)) > 300 and float(bounds.get("Height", 0)) < 250:
                candidates.append((w.get("kCGWindowLayer", 0), title, owner, bounds))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x[0], "Integrated v97" in x[1]), reverse=True)
        return candidates[0][3]
    except Exception as exc:
        log_json(ACTIONS_LOG, {"type": "mouse_probe", "error": f"{type(exc).__name__}: {exc}"})
        return None


def post_mouse_click(x, y, hold=0.08, button=0, target_widget=None, fraction_x=None, fraction_y=None):
    try:
        log_json(CLICK_LOG, {"type": "INJECTED_CLICK", "x": round(float(x), 2), "y": round(float(y), 2),
                             "target_widget": target_widget, "fraction_x": fraction_x, "fraction_y": fraction_y,
                             "hold_seconds": hold, "button": "left" if button == 0 else "right",
                             "monotonic": time.monotonic(), "epoch": time.time()})
        import Quartz
        down_type = Quartz.kCGEventLeftMouseDown if button == 0 else Quartz.kCGEventRightMouseDown
        up_type = Quartz.kCGEventLeftMouseUp if button == 0 else Quartz.kCGEventRightMouseUp
        btn = Quartz.kCGMouseButtonLeft if button == 0 else Quartz.kCGMouseButtonRight
        p = (float(x), float(y))
        ev = Quartz.CGEventCreateMouseEvent(None, down_type, p, btn)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(hold)
        ev = Quartz.CGEventCreateMouseEvent(None, up_type, p, btn)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        return True
    except Exception as exc:
        log_json(ACTIONS_LOG, {"type": "mouse_click_error", "x": x, "y": y, "error": f"{type(exc).__name__}: {exc}"})
        return False


def read_widget_geometry():
    try:
        payload = json.loads(GEOMETRY.read_text(encoding="utf-8"))
        return payload.get("widgets", {})
    except Exception:
        return {}


def click_widget_point(name, fx=0.5, fy=0.5, hold=0.08, button=0):
    widgets = read_widget_geometry()
    w = widgets.get(name)
    if not w or w.get("width", 0) <= 1 or w.get("height", 0) <= 1:
        return False
    # Stay 3% inside the requested edge to avoid clicking outside due to rounding.
    fx = min(0.97, max(0.03, float(fx)))
    fy = min(0.97, max(0.03, float(fy)))
    x = float(w["root_x"]) + float(w["width"]) * fx
    y = float(w["root_y"]) + float(w["height"]) * fy
    return post_mouse_click(x, y, hold=hold, button=button, target_widget=name,
                            fraction_x=round(fx, 3), fraction_y=round(fy, 3))


def click_widget_center(name, hold=0.08, button=0):
    return click_widget_point(name, 0.5, 0.5, hold=hold, button=button)


def dismiss_test_popup_if_present(bar_pid=None):
    hb = read_heartbeat() or {}
    if hb.get("timer_editor_open") or hb.get("context_menu_mapped"):
        if bar_pid is not None and not bring_process_frontmost(bar_pid):
            log_json(ACTIONS_LOG, {"type": "popup_dismiss", "result": "skipped", "reason": "bar foreground not verified; Escape not sent"})
            return
        send_keycode(53)  # Escape only when the diagnostic UI confirms a popup is open and bar is foreground.
        time.sleep(0.3)
    # Timer editor has a diagnostic-only Escape binding that destroys only the editor.


def click_delivery_metrics(records):
    injections = [r for r in records if r.get("type") == "INJECTED_CLICK" and isinstance(r.get("monotonic"), (int, float))]
    events = [r for r in records if r.get("event_type") is not None and
              isinstance(r.get("monotonic"), (int, float)) and
              isinstance(r.get("root_x"), (int, float)) and isinstance(r.get("root_y"), (int, float))]
    used = set()
    matched = []
    by_target = {}
    by_target_point = {}
    for injection in injections:
        target = str(injection.get("target_widget") or "unknown")
        point_key = f"{target}@{injection.get('fraction_x','?')},{injection.get('fraction_y','?')}"
        by_target.setdefault(target, {"injected": 0, "delivered": 0})["injected"] += 1
        by_target_point.setdefault(point_key, {"injected": 0, "delivered": 0})["injected"] += 1
        ix, iy, it = float(injection.get("x", -99999)), float(injection.get("y", -99999)), float(injection["monotonic"])
        candidates = []
        for event_index, event in enumerate(events):
            if event_index in used: continue
            delta = float(event["monotonic"]) - it
            if delta < -0.03 or delta > 1.0: continue
            dx = float(event["root_x"]) - ix
            dy = float(event["root_y"]) - iy
            distance = (dx * dx + dy * dy) ** 0.5
            if distance <= 20:
                candidates.append((abs(delta) + distance / 100.0, event_index, event, delta, distance))
        if candidates:
            _, event_index, event, delta, distance = min(candidates, key=lambda x: x[0])
            used.add(event_index)
            matched.append({"target_widget": target, "target_fraction": [injection.get("fraction_x"), injection.get("fraction_y")],
                            "injection_monotonic": it, "event_monotonic": event.get("monotonic"),
                            "event_type": event.get("event_type"), "actual_widget": event.get("widget"),
                            "delta_seconds": round(delta, 4), "distance_pixels": round(distance, 2)})
            by_target[target]["delivered"] += 1
            by_target_point[point_key]["delivered"] += 1
    total = len(injections)
    delivered = len(matched)
    for group in (by_target, by_target_point):
        for item in group.values():
            item["delivery_ratio"] = round(item["delivered"] / item["injected"], 4) if item["injected"] else None
    return {"injected_attempts": total, "matched_tk_deliveries": delivered,
            "unmatched_injections": total - delivered,
            "delivery_ratio": round(delivered / total, 4) if total else None,
            "tk_events": len(events), "unmatched_tk_events": len(events) - len(used),
            "delivery_by_target": by_target, "delivery_by_target_point": by_target_point,
            "matches": matched}


def mouse_probe(index, bar_pid=None):
    try: click_log_start_offset = CLICK_LOG.stat().st_size
    except OSError: click_log_start_offset = 0
    if bar_pid is not None:
        if not bring_process_frontmost(bar_pid):
            log_json(ACTIONS_LOG, {"type": "mouse_probe", "index": index, "result": "skipped", "reason": "could not confirm bar foreground; no clicks sent"})
            activate_jamzone()
            return False
        time.sleep(0.25)
    geometry = read_widget_geometry()
    required = ("session_timer_label", "timer_toggle_button", "timer_reset_button", "session_tile")
    missing = [name for name in required if name not in geometry]
    if missing:
        log_json(ACTIONS_LOG, {"type": "mouse_probe", "index": index, "result": "actual Tk widget geometry missing", "missing": missing})
        if bar_pid is not None: activate_jamzone()
        return False
    before = read_heartbeat() or {}
    click_receipts = []
    # Sample center and four inner hit-area points on the timer text and both buttons.
    # Wait long enough for the 260 ms single-click debounce before the next click.
    hit_points = ((0.5, 0.5), (0.12, 0.5), (0.88, 0.5), (0.5, 0.15), (0.5, 0.85))
    for widget_name in ("session_timer_label", "timer_toggle_button", "timer_reset_button"):
        for fx, fy in hit_points:
            if widget_name == "timer_toggle_button":
                click_widget_center("timer_reset_button")
                time.sleep(0.25)
            elif widget_name == "timer_reset_button":
                click_widget_center("timer_reset_button")
                time.sleep(0.25)
                click_widget_center("timer_toggle_button")
                time.sleep(0.35)
            state_before = (read_heartbeat() or {}).get("timer_running")
            injected = click_widget_point(widget_name, fx, fy)
            time.sleep(0.70)
            state_after = (read_heartbeat() or {}).get("timer_running")
            if widget_name == "timer_toggle_button":
                functional_result = bool(injected and state_after is True)
            elif widget_name == "timer_reset_button":
                functional_result = bool(injected and state_before is True and state_after is False)
            else:
                functional_result = bool(injected and state_before != state_after)
            click_receipts.append({"widget": widget_name, "fx": fx, "fy": fy, "injected": injected,
                                   "timer_running_before": state_before, "timer_running_after": state_after,
                                   "functional_state_change": functional_result})
            if widget_name != "session_timer_label":
                click_widget_center("timer_reset_button")
                time.sleep(0.35)
    # Reset after text-area clicks so the timer cannot remain active from the probe.
    click_widget_center("timer_reset_button")
    time.sleep(0.35)
    # Double-click and long-press are tested separately on the actual timer text widget.
    double1 = click_widget_center("session_timer_label", hold=0.05)
    time.sleep(0.12)
    double2 = click_widget_center("session_timer_label", hold=0.05)
    time.sleep(1.1)
    hb_double = read_heartbeat() or {}
    editor_entry_key = next((k for k, v in read_widget_geometry().items()
                             if k.startswith("widget:") and v.get("class") == "Entry" and v.get("width", 0) > 1), None)
    entry_click = click_widget_center(editor_entry_key) if hb_double.get("timer_editor_open") and editor_entry_key else False
    time.sleep(1.1)
    hb_entry = read_heartbeat() or {}
    dismiss_test_popup_if_present(bar_pid)
    long_press = click_widget_center("session_timer_label", hold=0.85)
    time.sleep(1.1)
    hb_long = read_heartbeat() or {}
    dismiss_test_popup_if_present(bar_pid)
    # Probe five inner hit-area points on every status/effect tile; these are passive state displays.
    status_effect_widgets = [name for name in geometry if name.startswith("status_tile:") or name.startswith("effect_tile:")]
    passive_hit_area_receipts = []
    for name in status_effect_widgets:
        for fx, fy in hit_points:
            injected = click_widget_point(name, fx, fy)
            passive_hit_area_receipts.append({"widget": name, "fx": fx, "fy": fy, "injected": injected})
            time.sleep(0.12)
    passive_widgets = [name for name in ("midi_tile", "bpm_tile", "reminder_tile", "reminder_label", "clock_label", "mode_label", "fade_tile") if name in geometry]
    passive_receipts = []
    for name in passive_widgets:
        injected = click_widget_center(name)
        passive_receipts.append({"widget": name, "injected": injected})
        time.sleep(0.12)
    # Right-click a known widget, then click the safe Show bar menu item if it is actually mapped.
    right = click_widget_center("session_tile", hold=0.06, button=1)
    time.sleep(1.1)
    hb_right = read_heartbeat() or {}
    menu_item_geometry = read_widget_geometry().get("context_menu:show_bar")
    menu_item_clicked = click_widget_center("context_menu:show_bar") if hb_right.get("context_menu_mapped") and menu_item_geometry else False
    time.sleep(0.8)
    hb_menu_item = read_heartbeat() or {}
    dismiss_test_popup_if_present(bar_pid)
    click_widget_center("timer_reset_button")
    time.sleep(0.3)
    after_probe = read_heartbeat() or {}
    try:
        with CLICK_LOG.open("rb") as _cf:
            _cf.seek(click_log_start_offset)
            _probe_records = [json.loads(_line.decode("utf-8")) for _line in _cf if _line.strip()]
    except Exception: _probe_records = []
    delivery = click_delivery_metrics(_probe_records)
    timer_point_checks = [r for r in click_receipts if r["widget"] in ("session_timer_label", "timer_toggle_button", "timer_reset_button")]
    functional_passes = sum(1 for r in timer_point_checks if r.get("functional_state_change"))
    probe_success = bool(delivery["injected_attempts"] and delivery["delivery_ratio"] is not None and
                         delivery["delivery_ratio"] >= 0.80 and functional_passes >= 12)
    log_json(ACTIONS_LOG, {"type": "mouse_probe", "index": index,
        "geometry_widget_count": len(geometry), "hit_area_clicks": click_receipts,
        "status_effect_tile_hit_area_clicks": passive_hit_area_receipts,
        "passive_tile_clicks": passive_receipts, "double_click_injected": [double1, double2],
        "long_press_injected": long_press, "right_click_injected": right,
        "click_events_before": before.get("click_events_seen"),
        "click_events_after": after_probe.get("click_events_seen"),
        "tk_event_delta": (after_probe.get("click_events_seen", 0) - before.get("click_events_seen", 0)),
        "click_delivery": delivery,
        "probe_success_threshold": 0.80, "timer_functional_points_passed": functional_passes,
        "timer_functional_points_total": len(timer_point_checks), "probe_success": probe_success,
        "timer_editor_after_double": hb_double.get("timer_editor_open"),
        "timer_editor_entry_click_injected": entry_click,
        "focus_after_entry_click": hb_entry.get("focused_widget"),
        "timer_editor_after_long_press": hb_long.get("timer_editor_open"),
        "context_menu_after_right_click": hb_right.get("context_menu_mapped"),
        "show_bar_menu_item_geometry_found": bool(menu_item_geometry),
        "show_bar_menu_item_click_injected": menu_item_clicked,
        "context_menu_after_show_bar_click": hb_menu_item.get("context_menu_mapped"),
        "note": "Delivery requires a Tk event near the injected screen coordinates within one second. This measures application event delivery, not mouse hardware health."})
    if bar_pid is not None:
        activate_jamzone()
    return probe_success


def set_diagnostic_timer_duration(seconds, phase_name, timeout=12):
    """Set a temporary timer duration through a diagnostic-only request; never saves user defaults."""
    request_id = f"{phase_name}_{int(time.time())}_{random.randint(1000,9999)}"
    try:
        write_json_atomic(TIMER_SETUP_RESULT, {"request_id": request_id, "status": "pending"})
        tmp = TIMER_SETUP_REQUEST.with_suffix(".tmp")
        tmp.write_text(json.dumps({"request_id": request_id, "seconds": int(seconds)}), encoding="utf-8")
        os.replace(tmp, TIMER_SETUP_REQUEST)
    except Exception as exc:
        log_json(ACTIONS_LOG, {"type": "timer_setup_request_error", "phase": phase_name, "error": str(exc)})
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = json.loads(TIMER_SETUP_RESULT.read_text(encoding="utf-8"))
            if result.get("request_id") == request_id:
                success = result.get("status") == "applied" and int(result.get("seconds", -1)) == int(seconds)
                log_json(ACTIONS_LOG, {"type": "timer_duration_setup", "phase": phase_name, "requested_seconds": seconds,
                                       "result": result, "success": success, "saved_default_unchanged": True})
                return success
        except Exception:
            pass
        time.sleep(0.25)
    log_json(ACTIONS_LOG, {"type": "timer_duration_setup_timeout", "phase": phase_name, "requested_seconds": seconds})
    return False


def timer_phase_click(phase_name, duration_seconds=None, reset_after=False, bar_pid=None):
    if bar_pid is not None:
        if not bring_process_frontmost(bar_pid):
            log_json(ACTIONS_LOG, {"type": "timer_phase", "phase": phase_name, "success": False,
                                   "reason": "could not confirm bar foreground; no clicks sent"})
            activate_jamzone()
            return False
        time.sleep(0.25)
    setup_ok = True if duration_seconds is None else set_diagnostic_timer_duration(duration_seconds, phase_name)
    clicked = False
    running_verified = False
    reset_clicked = None
    if setup_ok and not reset_after:
        clicked = click_widget_center("timer_toggle_button")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (read_heartbeat() or {}).get("timer_running") is True:
                running_verified = True
                break
            time.sleep(0.25)
    elif reset_after:
        reset_clicked = click_widget_center("timer_reset_button")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (read_heartbeat() or {}).get("timer_running") is False:
                running_verified = True
                break
            time.sleep(0.25)
    log_json(ACTIONS_LOG, {"type": "timer_phase", "phase": phase_name,
        "duration_seconds": duration_seconds, "duration_setup_ok": setup_ok,
        "toggle_click_injected": clicked, "timer_running_verified": running_verified,
        "reset_click_injected": reset_clicked, "reset_after": reset_after,
        "note": "Duration is set only in the diagnostic copy on the Tk thread; saved user defaults are not changed. Mouse injection and Tk event receipt are logged separately."})
    final_hb = read_heartbeat() or {}
    log_json(ACTIONS_LOG, {"type": "timer_phase_final_state", "phase": phase_name,
        "timer_running": final_hb.get("timer_running"),
        "timer_remaining_seconds": final_hb.get("timer_remaining_seconds"),
        "timer_expired": final_hb.get("timer_expired")})
    if bar_pid is not None:
        activate_jamzone()
    return setup_ok and (running_verified if not reset_after else (reset_clicked is not False and running_verified))


def manual_mouse_baseline(bar_pid):
    """Record real user mouse clicks for 20s before synthetic click probes begin."""
    if not bring_process_frontmost(bar_pid):
        log_json(ACTIONS_LOG, {"type": "manual_mouse_baseline", "result": "skipped", "reason": "could not confirm bar foreground"})
        activate_jamzone()
        return
    before = read_heartbeat() or {}
    start = dt.datetime.now().astimezone().isoformat(timespec="milliseconds")
    note("MANUAL MOUSE BASELINE: for 20 seconds, click only the timer text 10 times; try two double-clicks and two long-presses. Do not click other controls. This distinguishes physical mouse delivery from injected test clicks.")
    time.sleep(20)
    end = dt.datetime.now().astimezone().isoformat(timespec="milliseconds")
    after = read_heartbeat() or {}
    log_json(ACTIONS_LOG, {"type": "manual_mouse_baseline", "result": "completed", "start": start, "end": end,
        "tk_event_count_before": before.get("click_events_seen"), "tk_event_count_after": after.get("click_events_seen"),
        "tk_event_delta": (after.get("click_events_seen", 0) - before.get("click_events_seen", 0)),
        "timer_editor_open_after": after.get("timer_editor_open"),
        "context_menu_open_after": after.get("context_menu_mapped")})
    dismiss_test_popup_if_present(bar_pid)
    if bring_process_frontmost(bar_pid):
        click_widget_center("timer_reset_button")
        time.sleep(0.4)
    activate_jamzone()


def send_cc(outport, channel, control, value):
    import mido
    outport.send(mido.Message("control_change", channel=channel, control=control, value=value))


def send_note_press_release(outport, channel, note):
    import mido
    outport.send(mido.Message("note_on", channel=channel, note=note, velocity=127))
    time.sleep(0.18)
    outport.send(mido.Message("note_off", channel=channel, note=note, velocity=0))


def bus2_feedback_count_since(offset, kind, number):
    try:
        with BUS2_LOG.open("rb") as f:
            f.seek(offset)
            rows = [json.loads(line.decode("utf-8")) for line in f if line.strip()]
        if kind == "cc":
            note_alias = {42: 2, 45: 0, 39: 3, 40: 11}.get(number)
            return sum(1 for row in rows if
                       (row.get("type") == "control_change" and row.get("channel") == 14 and row.get("control") == number)
                       or (note_alias is not None and row.get("type") in ("note_on", "note_off") and row.get("note") == note_alias))
        return sum(1 for row in rows if row.get("type") in ("note_on", "note_off") and row.get("note") == number)
    except Exception:
        return 0


def action_midi_toggle(outport, kind, number):
    # Controller output uses MainStage mappings on IAC Bus 1; measure Bus 2 feedback separately.
    try: before_offset = BUS2_LOG.stat().st_size
    except OSError: before_offset = 0
    if kind == "cc":
        # Two explicit press/release pulses, spaced for MainStage to emit state mirrors.
        send_cc(outport, 14, number, 127)
        time.sleep(0.18)
        send_cc(outport, 14, number, 0)
        time.sleep(0.30)
        send_cc(outport, 14, number, 127)
        time.sleep(0.18)
        send_cc(outport, 14, number, 0)
    elif kind == "note":
        # MainStage Bus 1 mappings in v97 use outgoing MIDI channel 15.
        send_note_press_release(outport, 14, number)
        time.sleep(0.25)
        send_note_press_release(outport, 14, number)
    time.sleep(1.0)
    feedback_count = bus2_feedback_count_since(before_offset, kind, number)
    result = {"kind": kind, "number": number, "bus2_feedback_count": feedback_count,
              "bus2_feedback_seen": feedback_count > 0,
              "bus2_feedback_minimum_expected": 2,
              "bus2_feedback_complete": feedback_count >= 2,
              "feedback_log": BUS2_LOG.name}
    log_json(ACTIONS_LOG, {"type": "midi_state_feedback", **result})
    return result


def action_volume(outport, cc, cycle):
    values = [127, 104, 78, 52, 78, 104, 127]
    for v in values:
        send_cc(outport, 14, cc, v)
        time.sleep(0.65)
    log_json(ACTIONS_LOG, {"type": "volume_cycle", "cc": cc, "cycle": cycle, "values": values, "restored_to": 127})


def action_g2m_repeat(testport, cycle):
    send_cc(testport, 15, 44, 0)
    time.sleep(0.8)
    send_cc(testport, 15, 42, 127)
    time.sleep(0.4)
    send_cc(testport, 15, 42, 127)
    time.sleep(5.5)
    state = (read_heartbeat() or {}).get("controller_state", {})
    returned_to_idle = bool(state) and not state.get("fade_active") and not state.get("scenario_busy") and state.get("awaiting_tt")
    log_json(ACTIONS_LOG, {"type": "g2m_repeat_test", "cycle": cycle, "sequence": ["CH16 CC44", "CH16 CC42", "CH16 CC42 repeat"],
                           "post_scenario_state": state, "returned_to_idle": returned_to_idle})
    return returned_to_idle


def action_tb_repeat(testport, cycle):
    send_cc(testport, 15, 44, 0)
    time.sleep(0.8)
    send_cc(testport, 15, 45, 127)
    time.sleep(0.4)
    send_cc(testport, 15, 45, 127)
    time.sleep(5.5)
    state = (read_heartbeat() or {}).get("controller_state", {})
    returned_to_idle = bool(state) and not state.get("fade_active") and not state.get("scenario_busy") and state.get("awaiting_tt")
    log_json(ACTIONS_LOG, {"type": "tb_repeat_test", "cycle": cycle, "sequence": ["CH16 CC44", "CH16 CC45", "CH16 CC45 repeat"],
                           "post_scenario_state": state, "returned_to_idle": returned_to_idle})
    return returned_to_idle


def frontmost_process_info():
    rc_id, out_id, err_id = apple_script('tell application "System Events" to get unix id of first process whose frontmost is true')
    rc_name, out_name, err_name = apple_script('tell application "System Events" to get name of first process whose frontmost is true')
    try: pid = int(out_id.strip()) if rc_id == 0 else None
    except ValueError: pid = None
    return {"pid": pid, "name": out_name.strip() if rc_name == 0 else None,
            "id_error": err_id, "name_error": err_name}


def bring_process_frontmost(pid):
    script = f'tell application "System Events" to tell (first process whose unix id is {pid}) to set frontmost to true'
    rc, out, err = apple_script(script)
    time.sleep(0.15)
    actual = frontmost_process_info()
    verified = rc == 0 and actual.get("pid") == int(pid)
    log_json(ACTIONS_LOG, {"type": "bring_process_frontmost", "pid": pid, "rc": rc, "stdout": out,
                           "stderr": err, "actual_frontmost": actual, "verified": verified})
    return verified


def send_jamzone_hid_key(key: str):
    """Send a physical US-position P/L key only after JamZone is foregrounded for 0.5s."""
    key = key.lower()
    keycodes = {"p": 35, "l": 37}
    if key not in keycodes:
        raise ValueError(f"Unsupported JamZone HID key: {key!r}")
    if not activate_jamzone():
        log_json(ACTIONS_LOG, {"type": "jamzone_hid", "key": key, "result": "activation_failed"})
        return False
    time.sleep(0.5)
    actual = frontmost_process_info()
    if "jamzone" not in str(actual.get("name") or "").lower():
        log_json(ACTIONS_LOG, {"type": "jamzone_hid", "key": key, "result": "skipped", "reason": "JamZone no longer verified as frontmost", "actual_frontmost": actual})
        return False
    rc, out, err = send_keycode(keycodes[key])
    log_json(ACTIONS_LOG, {"type": "jamzone_hid", "key": key, "physical_keycode": keycodes[key],
                           "foreground_wait_seconds": 0.5, "actual_frontmost": actual,
                           "rc": rc, "stdout": out, "stderr": err})
    if rc == 0:
        if key == "l": update_run_state(loop_restore_needed=False)
        if key == "p": update_run_state(jamzone_restore_needed=False, loop_action_log_start=None)
    return rc == 0


def action_quick_fade(testport, cycle):
    # Keep JamZone frontmost so the controller's own P key targets playback.
    if not activate_jamzone():
        log_json(ACTIONS_LOG, {"type": "quick_fade_skipped", "cycle": cycle, "reason": "JamZone activation failed"})
        return False
    time.sleep(0.5)
    update_run_state(jamzone_restore_needed=True, loop_restore_needed=False, loop_action_log_start=None)
    # CC44 starts TT1; CC22 triggers the existing quick-fade + P action.
    send_cc(testport, 15, 44, 0)
    time.sleep(0.8)
    send_cc(testport, 15, 22, 127)
    log_json(ACTIONS_LOG, {"type": "quick_fade_and_stop", "cycle": cycle, "sequence": ["CH16 CC44", "CH16 CC22"], "jamzone_may_stop": True})
    time.sleep(3.0)
    restored = attempt_jamzone_play_restore()
    log_json(ACTIONS_LOG, {"type": "quick_fade_playback_restore", "cycle": cycle, "physical_P_dispatch_succeeded": restored})
    return restored


def action_loop_fade(testport, cycle, bar_pid):
    try: before_log_size = CHILD_LOG.stat().st_size
    except OSError: before_log_size = 0
    # Start TT1, then send Down only after confirming the diagnostic bar is frontmost.
    if not activate_jamzone():
        log_json(ACTIONS_LOG, {"type": "loop_fade_skipped", "cycle": cycle, "reason": "JamZone activation failed"})
        return False
    update_run_state(jamzone_restore_needed=True, loop_restore_needed=False, loop_action_log_start=before_log_size)
    send_cc(testport, 15, 44, 0)
    time.sleep(0.8)
    if not bring_process_frontmost(bar_pid):
        # Cancel the pending full fade before it can stop playback.
        send_cc(testport, 15, 44, 0)
        attempt_jamzone_play_restore()
        log_json(ACTIONS_LOG, {"type": "loop_fade_aborted", "cycle": cycle, "reason": "could not foreground bar; TT cancellation sent"})
        return False
    time.sleep(0.25)
    key_rc, key_out, key_err = send_keycode(125)  # Down arrow; existing v97 listener starts down_sequence during TT1.
    deadline = time.monotonic() + 2.0
    down_started = False
    while time.monotonic() < deadline:
        state = (read_heartbeat() or {}).get("controller_state", {})
        if state.get("down_sequence_active"):
            down_started = True
            break
        time.sleep(0.1)
    if key_rc != 0 or not down_started:
        # Avoid allowing a failed Down delivery to fall through to a full fade.
        send_cc(testport, 15, 44, 0)
        time.sleep(0.25)
        try:
            with CHILD_LOG.open("rb") as _logf:
                _logf.seek(before_log_size)
                _recent_controller_log = _logf.read().decode("utf-8", errors="replace")
            controller_l_already_sent = "[hid sent] key=l" in _recent_controller_log.lower()
        except Exception:
            controller_l_already_sent = False
        update_run_state(loop_restore_needed=bool(controller_l_already_sent))
        loop_restored = send_jamzone_hid_key("l") if controller_l_already_sent else None
        playback_restored = send_jamzone_hid_key("p")
        log_json(ACTIONS_LOG, {"type": "loop_fade_aborted", "cycle": cycle, "key_rc": key_rc,
            "key_stdout": key_out, "key_stderr": key_err, "down_sequence_observed": down_started,
            "controller_l_already_sent": controller_l_already_sent,
            "loop_restore_HID_dispatch_succeeded": loop_restored,
            "playback_HID_dispatch_succeeded": playback_restored,
            "reason": "Down sequence not confirmed; second TT cancellation attempted; extra L sent only if controller log proves L already fired"})
        return False
    time.sleep(3.2)
    try:
        with CHILD_LOG.open("rb") as _logf:
            _logf.seek(before_log_size)
            _loop_chunk = _logf.read().decode("utf-8", errors="replace").lower()
        controller_l_sent = "[hid sent] key=l" in _loop_chunk
    except Exception:
        controller_l_sent = False
    update_run_state(loop_restore_needed=controller_l_sent)
    # Only send the extra L if the controller's own log confirms it already sent L.
    # Otherwise an extra L would turn an intact loop OFF.
    loop_restored = send_jamzone_hid_key("l") if controller_l_sent else True
    playback_restored = send_jamzone_hid_key("p")
    update_run_state(jamzone_restore_needed=False, loop_restore_needed=False, loop_action_log_start=None)
    log_json(ACTIONS_LOG, {"type": "loop_fade_test", "cycle": cycle,
        "sequence": ["CH16 CC44", "foregrounded Down", "observed down_sequence_active", "controller P/L",
                     "extra foregrounded L only if controller L was confirmed", "foregrounded P playback restore"],
        "down_sequence_observed": down_started, "down_key_rc": key_rc,
        "controller_l_sent": controller_l_sent,
        "extra_l_sent": controller_l_sent,
        "loop_restore_HID_dispatch_succeeded": loop_restored,
        "playback_HID_dispatch_succeeded": playback_restored})
    return controller_l_sent and loop_restored and playback_restored

def action_full_fade(testport, cycle):
    # Keep JamZone frontmost for the controller's playback-stop P at fade completion.
    if not activate_jamzone():
        log_json(ACTIONS_LOG, {"type": "full_fade_skipped", "cycle": cycle, "reason": "JamZone activation failed"})
        return False
    time.sleep(0.5)
    update_run_state(jamzone_restore_needed=True, loop_restore_needed=False, loop_action_log_start=None)
    send_cc(testport, 15, 44, 0)
    log_json(ACTIONS_LOG, {"type": "full_fade_test_started", "cycle": cycle, "trigger": "CH16 CC44", "expected_duration_seconds": 20, "jamzone_stop_expected": True})
    time.sleep(22)
    restored = attempt_jamzone_play_restore()
    log_json(ACTIONS_LOG, {"type": "full_fade_test_complete", "cycle": cycle, "expected_master_restore": 127, "physical_P_dispatch_succeeded": restored})
    return restored


def attempt_jamzone_play_restore():
    """Restore JamZone playback using the confirmed physical P key, with focus delay."""
    return send_jamzone_hid_key("p")


def start_cpu_stress(worker_count, cycle):
    global ACTIVE_STRESS
    workers = []
    code = "import time\nd=time.monotonic()+60\nx=123456789\nwhile time.monotonic()<d:\n x=((x*1664525+1013904223)&0xffffffff)"
    for _ in range(worker_count):
        workers.append(subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True))
    ACTIVE_STRESS = workers[:]
    update_run_state(active_stress_pids=[p.pid for p in workers])
    log_json(ACTIONS_LOG, {"type": "cpu_stress_started", "cycle": cycle, "workers": worker_count,
                           "pids": [p.pid for p in workers], "duration_seconds": 60})
    return {"workers": workers, "end_at": time.monotonic() + 60, "cycle": cycle, "count": worker_count}


def stop_cpu_stress(stress, reason):
    global ACTIVE_STRESS
    if not stress: return
    for proc in stress.get("workers", []):
        if proc.poll() is None:
            try: proc.terminate(); proc.wait(timeout=2)
            except Exception:
                try: proc.kill()
                except Exception: pass
    ACTIVE_STRESS = []
    update_run_state(active_stress_pids=[])
    log_json(ACTIONS_LOG, {"type": "cpu_stress_stopped", "cycle": stress.get("cycle"),
                           "workers": stress.get("count"), "reason": reason})


def sample_system(child):
    rows = get_process_rows()
    top = [safe_process_row(r) for r in sorted(rows, key=lambda r: r["cpu_pct"], reverse=True)[:18]]
    # CoyoteMIDI is a required background component; record it even when its
    # CPU use is too low to appear in the top-18 process list. Store safe
    # process labels and resource numbers only, never command arguments/paths.
    coyotemidi_raw_rows = [r for r in rows
                           if "coyotemidi" in str(r.get("command", "")).casefold().replace(" ", "")]
    coyotemidi_rows = [safe_process_row(r) for r in coyotemidi_raw_rows]
    coyotemidi_present = bool(coyotemidi_raw_rows)
    if COYOTE_LAST_PRESENT[0] is None or coyotemidi_present != COYOTE_LAST_PRESENT[0]:
        event = "present" if coyotemidi_present else "missing"
        note(f"CoyoteMIDI presence changed: {event}.")
        log_json(ACTIONS_LOG, {"type": "coyotemidi_presence_change", "present": coyotemidi_present,
                               "process_count": len(coyotemidi_raw_rows), "sample_interval_seconds": 10})
        COYOTE_LAST_PRESENT[0] = coyotemidi_present
    coyotemidi_cpu_pct_total = round(sum(float(r.get("cpu_pct") or 0) for r in coyotemidi_raw_rows), 2)
    coyotemidi_rss_kb_total = sum(int(r.get("rss_kb") or 0) for r in coyotemidi_raw_rows)
    child_pid = child.pid if child else None
    child_raw = next((r for r in rows if r["pid"] == child_pid), None)
    child_row = safe_process_row(child_raw) if child_raw else None
    rc, out, err = run_capture(["sysctl", "-n", "hw.memsize"])
    total_mem = int(out) if rc == 0 and out.isdigit() else None
    try:
        import mido
        inputs = mido.get_input_names()
        outputs = mido.get_output_names()
        midi_ports = {"input_port_count": len(inputs), "output_port_count": len(outputs),
                      "airstep_present": any("AIRSTEP Bluetooth" in n for n in inputs),
                      "smc_present": any("SMC-Mixer Bluetooth" in n for n in (inputs + outputs)),
                      "iac_bus1_present": any("IAC Driver Bus 1" in n for n in (inputs + outputs)),
                      "iac_bus2_present": any("IAC Driver Bus 2" in n for n in (inputs + outputs)),
                      "port_names_recorded": False}
    except Exception as exc:
        midi_ports = {"error_type": type(exc).__name__, "port_names_recorded": False}
    log_json(SYSTEM_LOG, {"type": "resource_sample", "child_pid": child_pid, "child": child_row,
                          "top_processes": top, "coyotemidi_processes": coyotemidi_rows,
                          "coyotemidi_process_count": len(coyotemidi_rows),
                          "coyotemidi_present": coyotemidi_present,
                          "coyotemidi_cpu_pct_total": coyotemidi_cpu_pct_total,
                          "coyotemidi_rss_kb_total": coyotemidi_rss_kb_total,
                          "physical_memory_bytes": total_mem,
                          "midi_ports": midi_ports, "heartbeat": read_heartbeat(),
                          "restart_count": supervisor_restarts[0], "guardian_restart_count": min(GUARDIAN_RESTARTS[0], MAX_GUARDIAN_RESTARTS)})


def read_heartbeat():
    try:
        return json.loads(HEARTBEAT.read_text(encoding="utf-8"))
    except Exception:
        return None


def start_child(disable_internal_watchdog=False):
    global ACTIVE_CHILD
    write_json_atomic(HEARTBEAT, {"monotonic": 0.0, "pid": None, "reset_marker": time.monotonic()})
    # Minimal child environment: no inherited proxy/auth/cloud variables are passed to the diagnostic controller.
    env = minimal_environment()
    env["FLOATING_BAR_NIGHT_TEST_DIR"] = str(OUTDIR)
    env["PYTHONUNBUFFERED"] = "1"
    if disable_internal_watchdog:
        env["FLOATING_BAR_NIGHT_TEST_DISABLE_INTERNAL_WATCHDOG"] = "1"
    else:
        env.pop("FLOATING_BAR_NIGHT_TEST_DISABLE_INTERNAL_WATCHDOG", None)
    log_start_size = CHILD_LOG.stat().st_size if CHILD_LOG.exists() else 0
    f = CHILD_LOG.open("a", encoding="utf-8", buffering=1)
    child = subprocess.Popen([sys.executable, "-u", str(DIAG_COPY)], cwd=str(SCRIPT_DIR), env=env,
                             stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    child._night_log_handle = f
    child._night_started_at = time.monotonic()
    child._night_log_start_size = log_start_size
    ACTIVE_CHILD = child
    update_run_state(diagnostic_pid=child.pid)
    note(f"Started diagnostic Floating Bar PID={child.pid}; stdout log={CHILD_LOG.name}; internal_watchdog_disabled={disable_internal_watchdog}")
    return child


def wait_for_diagnostic_ready(child, timeout=30):
    """Require the virtual test port plus both physical/Bus2 receivers after every restart."""
    deadline = time.monotonic() + timeout
    last_port = None
    airstep_opened = False
    bus2_opened = False
    while time.monotonic() < deadline:
        if child.poll() is not None:
            return None, False, False
        try:
            names = __import__("mido").get_output_names()
            last_port = next((n for n in names if TEST_INPUT_NAME in n), None)
            with CHILD_LOG.open("r", encoding="utf-8", errors="replace") as _f:
                _f.seek(getattr(child, "_night_log_start_size", 0))
                log = _f.read()
            airstep_opened = ("[BT MIDI RECOVERY] Opened MIDI input: AIRSTEP" in log or
                              "[BT MIDI RECOVERY] Reopened MIDI input after error: AIRSTEP" in log)
            bus2_opened = "MIDI diagnostic input: IAC Bus 2" in log
            if last_port and airstep_opened and bus2_opened:
                return last_port, True, True
        except Exception:
            pass
        time.sleep(1)
    return last_port, airstep_opened, bus2_opened


def reopen_test_output(testout, timeout=20):
    try:
        if testout is not None: testout.close()
    except Exception: pass
    time.sleep(1.0)
    try:
        import mido
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            name = next((n for n in mido.get_output_names() if TEST_INPUT_NAME in n), None)
            if name:
                try: return mido.open_output(name), name
                except Exception: pass
            time.sleep(1.0)
    except Exception: pass
    return None, None


def stop_child(child, reason):
    global ACTIVE_CHILD
    if child is None or child.poll() is not None:
        if ACTIVE_CHILD is child: ACTIVE_CHILD = None
        return
    note(f"Stopping diagnostic Floating Bar PID={child.pid}: {reason}")
    try:
        child.terminate()
        child.wait(timeout=8)
    except subprocess.TimeoutExpired:
        child.kill()
        try: child.wait(timeout=5)
        except Exception: pass
    except Exception:
        pass
    try: child._night_log_handle.close()
    except Exception: pass
    if ACTIVE_CHILD is child:
        ACTIVE_CHILD = None
        update_run_state(diagnostic_pid=None)


def emergency_cleanup():
    global ACTIVE_CHILD, ACTIVE_STRESS
    for proc in ACTIVE_STRESS:
        if proc.poll() is None:
            try: proc.terminate(); proc.wait(timeout=2)
            except Exception:
                try: proc.kill()
                except Exception: pass
    ACTIVE_STRESS = []
    if not CONTROLLER_SWAP_STARTED[0]:
        return
    child = ACTIVE_CHILD
    if child is not None and child.poll() is None:
        try:
            hb = read_heartbeat() or {}
            if hb.get("timer_running") and bring_process_frontmost(child.pid):
                clicked = click_widget_center("timer_reset_button")
                log_json(ACTIONS_LOG, {"type": "emergency_timer_reset", "click_injected": clicked})
                time.sleep(0.3)
                activate_jamzone()
            elif hb.get("timer_running"):
                log_json(ACTIONS_LOG, {"type": "emergency_timer_reset", "click_injected": False, "reason": "bar foreground could not be confirmed; no mouse event sent"})
        except Exception: pass
        try:
            child.terminate(); child.wait(timeout=5)
        except Exception:
            try: child.kill()
            except Exception: pass
        try: child._night_log_handle.close()
        except Exception: pass
        ACTIVE_CHILD = None
    if RUN_STATE_DATA.get("jamzone_restore_needed") or RUN_STATE_DATA.get("loop_restore_needed"):
        try:
            loop_needed = bool(RUN_STATE_DATA.get("loop_restore_needed"))
            log_offset = RUN_STATE_DATA.get("loop_action_log_start")
            if not loop_needed and isinstance(log_offset, int):
                try:
                    with CHILD_LOG.open("rb") as _logf:
                        _logf.seek(max(0, log_offset))
                        loop_needed = "[hid sent] key=l" in _logf.read().decode("utf-8", errors="replace").lower()
                except Exception: pass
            if loop_needed: send_jamzone_hid_key("l")
            send_jamzone_hid_key("p")
            note("Emergency JamZone playback/loop restoration attempted based on the interrupted scenario state.")
        except Exception as exc:
            note(f"Emergency JamZone restoration failed: {type(exc).__name__}: {exc}")
    if LIVE_TEST_STARTED[0]:
        try:
            import mido
            outputs = mido.get_output_names()
            bus_name = next((n for n in outputs if n == "IAC Driver Bus 1" or "IAC Driver Bus 1" in n), None)
            if bus_name:
                restore_port = mido.open_output(bus_name)
                restore_port.send(mido.Message("control_change", channel=14, control=20, value=127))
                restore_port.send(mido.Message("control_change", channel=14, control=21, value=127))
                restore_port.close()
                note("Emergency MIDI restore sent: IAC Bus 1 CH15 CC20=127 and CC21=127 after stopping the diagnostic process.")
                LIVE_TEST_STARTED[0] = False
                update_run_state(live_test_started=False, active_stress_pids=[], diagnostic_pid=None)
            else:
                note("Emergency MIDI restore could not run: IAC Driver Bus 1 output unavailable.")
        except Exception as exc:
            try: note(f"Emergency MIDI volume restore failed: {type(exc).__name__}: {exc}")
            except Exception: pass
    # v16 intentionally does not relaunch v97 automatically. The original v97 file is preserved and can be started manually.
    try:
        cleanup_ok = bool(not LIVE_TEST_STARTED[0] and
                          not RUN_STATE_DATA.get("jamzone_restore_needed") and not RUN_STATE_DATA.get("loop_restore_needed"))
        update_run_state(terminal_cleanup_complete=cleanup_ok,
                         run_outcome="interrupted_cleanup_completed_manual_v97_restart" if cleanup_ok else "interrupted_cleanup_incomplete_manual_v97_restart")
        note("v16: automatic v97 relaunch skipped by user authorization; original v97 file remains available for manual restart.")
    except Exception: pass
    try:
        if ESC_ABORT_REQUESTED[0]:
            note("Esc abort: no partial report ZIP created; minimal local cleanup/recovery logs are retained.")
            return
        sanitize_generated_text_files()
        zip_path = OUTDIR.with_suffix(".zip")
        if OUTDIR.exists() and not zip_path.exists():
            import zipfile
            with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED) as zf:
                for file in OUTDIR.rglob("*"):
                    if not file.is_file():
                        continue
                    rel = file.relative_to(OUTDIR)
                    if rel.parts and rel.parts[0] == "runtime":
                        continue
                    if file.name == "test_message_settings.json":
                        continue
                    if file.name.endswith(".tmp"):
                        continue
                    zf.write(file, file.relative_to(OUTDIR.parent))
            note(f"Partial diagnostic report packaged after interruption: {zip_path}")
    except Exception as exc:
        try: note(f"Could not package partial report: {type(exc).__name__}: {exc}")
        except Exception: pass


def close_existing_controller():
    # Stop only the known controller source processes; never unrelated Python processes.
    rows = get_process_rows()
    pids = [r["pid"] for r in rows if is_controller_row(r, "v96") or is_controller_row(r, "v97")]
    own_pid = os.getpid()
    for pid in pids:
        if pid == own_pid:
            continue
        note(f"Stopping existing v96/v97 controller process PID={pid} before starting the separate diagnostic copy.")
        try: os.kill(pid, signal.SIGTERM)
        except ProcessLookupError: pass
    deadline = time.time() + 10
    while time.time() < deadline:
        if not any(pid in get_controller_pids() for pid in pids):
            break
        time.sleep(0.5)
    for pid in pids:
        try: os.kill(pid, 0)
        except ProcessLookupError: continue
        # Do not force-kill unrelated or renamed processes; exact original match was captured above.
        try: os.kill(pid, signal.SIGKILL); note(f"Controller PID={pid} required SIGKILL after SIGTERM timeout.")
        except ProcessLookupError: pass


def create_guardian_script():
    if GUARDIAN_SCRIPT.exists():
        raise RuntimeError("Guardian destination already exists; refusing to overwrite any file.")
    guardian_source = r'''#!/usr/bin/env python3
import json, os, signal, subprocess, sys, time, hashlib
from pathlib import Path
parent_pid = int(sys.argv[1])
state_path = Path(sys.argv[2])
source_path = Path(sys.argv[3])
run_dir = Path(sys.argv[4])
expected_hash = sys.argv[5]
log_path = run_dir / "guardian.log"
diag_name = "mainstage_jamzone_fade_controller_FLOATING_BAR_v97_NIGHTTEST.py"
def log(event, **data):
    payload = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **data}
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n"); f.flush()
    except Exception: pass
def read_state():
    try: return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception: return {}
def write_state(st):
    tmp = state_path.with_suffix(".guardian.tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    os.replace(tmp, state_path)
def process_rows():
    try:
        p = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=8)
        rows=[]
        if p.returncode == 0:
            for line in p.stdout.splitlines():
                bits=line.strip().split(None,1)
                if len(bits)==2:
                    try: rows.append((int(bits[0]), bits[1]))
                    except ValueError: pass
        return rows
    except Exception: return []
def command_for(pid, rows=None):
    rows = rows if rows is not None else process_rows()
    return next((cmd for p,cmd in rows if p==pid), "")
def executed_script(cmd):
    import shlex
    try: tokens=shlex.split(cmd)
    except Exception: tokens=cmd.split()
    for token in tokens[1:]:
        if token.lower().endswith(".py"): return os.path.basename(token)
    return None
def is_v96(cmd): return executed_script(cmd)=="mainstage_jamzone_fade_controller_FLOATING_BAR_v96.py"
def is_v97(cmd): return executed_script(cmd)=="mainstage_jamzone_fade_controller_FLOATING_BAR_v97.py"
def parent_alive():
    cmd = command_for(parent_pid)
    if executed_script(cmd) != "floating_bar_v97_night_test_v16.py":
        return False
    try:
        hb = json.loads((run_dir / "runner_heartbeat.json").read_text(encoding="utf-8"))
        age = max(0.0, time.monotonic() - float(hb.get("monotonic", 0.0)))
    except Exception:
        age = 999999.0
    # All normal test actions are bounded well below this threshold; a stale marker means the runner itself is stuck.
    if age <= 120.0:
        return True
    log("runner_heartbeat_stale", age_seconds=round(age, 2), action="terminate exact test runner and restore test state; v97 relaunch is manual")
    try:
        os.kill(parent_pid, signal.SIGTERM)
    except Exception:
        pass
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if executed_script(command_for(parent_pid)) != "floating_bar_v97_night_test_v16.py":
            return False
        time.sleep(0.25)
    if executed_script(command_for(parent_pid)) == "floating_bar_v97_night_test_v16.py":
        try:
            os.kill(parent_pid, signal.SIGKILL)
            log("runner_force_stopped_after_stale_heartbeat", pid=parent_pid)
        except Exception as exc:
            log("runner_force_stop_failed", error=type(exc).__name__)
    return False
def exact_controller_rows():
    rows=process_rows()
    return [(pid,cmd) for pid,cmd in rows if is_v96(cmd) or is_v97(cmd)]
def key_jamzone(key):
    code = 37 if key == "l" else 35
    try:
        subprocess.run(["osascript", "-e", 'tell application "Jamzone" to activate'], capture_output=True, text=True, timeout=8)
        time.sleep(0.6)
        p = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'], capture_output=True, text=True, timeout=8)
        if p.returncode != 0 or "jamzone" not in p.stdout.lower():
            log("jamzone_key_skipped", key=key, reason="JamZone not verified frontmost")
            return False
        p = subprocess.run(["osascript", "-e", f'tell application "System Events" to key code {code}'], capture_output=True, text=True, timeout=8)
        log("jamzone_key_sent", key=key, success=(p.returncode==0))
        return p.returncode == 0
    except Exception as exc:
        log("jamzone_key_error", key=key, error=type(exc).__name__)
        return False
def restore_volume():
    try:
        import mido
        name = next((n for n in mido.get_output_names() if n == "IAC Driver Bus 1" or "IAC Driver Bus 1" in n), None)
        if not name:
            log("volume_restore_failed", reason="IAC Bus 1 unavailable"); return False
        port = mido.open_output(name)
        port.send(mido.Message("control_change", channel=14, control=20, value=127))
        port.send(mido.Message("control_change", channel=14, control=21, value=127))
        port.close(); log("volume_restore_sent", cc20=127, cc21=127); return True
    except Exception as exc:
        log("volume_restore_error", error=type(exc).__name__); return False
def kill_if_exact(pid, expected_substring, rows):
    cmd = command_for(pid, rows)
    if not cmd or expected_substring not in cmd: return False
    try: os.kill(pid, signal.SIGTERM)
    except ProcessLookupError: return True
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        time.sleep(0.25)
        cmd2=command_for(pid)
        if not cmd2 or expected_substring not in cmd2: return True
    try:
        cmd2=command_for(pid)
        if expected_substring in cmd2: os.kill(pid, signal.SIGKILL)
    except Exception: pass
    return True
def package_report():
    try:
        import zipfile
        home_text = str(Path.home())
        for f in run_dir.rglob("*"):
            if not f.is_file() or (run_dir / "runtime") in f.parents: continue
            if f.name == "test_message_settings.json" or f.suffix.lower() not in {".log", ".json", ".jsonl", ".txt"}: continue
            try:
                raw=f.read_text(encoding="utf-8", errors="replace")
                safe=raw.replace(home_text, "~")
                for old,new in (("AIRSTEP Bluetooth", "AIRSTEP"), ("SMC-Mixer Bluetooth", "SMC"),
                                ("IAC Driver Bus 1", "IAC Bus 1"), ("IAC Driver Bus 2", "IAC Bus 2")):
                    safe=safe.replace(old,new)
                if safe != raw: f.write_text(safe, encoding="utf-8")
            except Exception: pass
        final_path=run_dir / "guardian_final_status.json"
        final_path.write_text(json.dumps({"state":read_state(),"time":time.strftime("%Y-%m-%d %H:%M:%S"),
                                          "network_access":"none","report_local_only":True}, indent=2), encoding="utf-8")
        zip_path=run_dir.with_suffix(".zip")
        mode="a" if zip_path.exists() else "w"
        with zipfile.ZipFile(zip_path, mode, compression=zipfile.ZIP_DEFLATED) as zf:
            existing=set(zf.namelist())
            for f in run_dir.rglob("*"):
                if not f.is_file(): continue
                rel=f.relative_to(run_dir)
                if rel.parts and rel.parts[0]=="runtime": continue
                if f.name=="test_message_settings.json" or f.name.endswith(".tmp") or f==zip_path: continue
                arc=str(f.relative_to(run_dir.parent))
                if arc not in existing: zf.write(f, arc)
    except Exception as exc: log("report_zip_update_failed", error=type(exc).__name__)
# Stay passive while the runner is healthy. On an abrupt runner exit, restore service once.
while True:
    st=read_state()
    if parent_alive():
        time.sleep(5); continue
    time.sleep(3)
    st=read_state()
    if st.get("normal_complete"):
        log("guardian_exit", reason="nine-hour test completed; no recovery required")
        package_report()
        break
    if not st.get("swap_started"):
        log("guardian_exit", reason="preflight ended before controller swap")
        break
    if st.get("abort_requested") or str(st.get("run_outcome", "")).startswith("interrupted_cleanup"):
        log("guardian_exit", reason="user abort; no report ZIP will be created or updated")
        break
    if st.get("terminal_cleanup_complete"):
        log("guardian_exit", reason="controller restoration already verified; packaging local report")
        package_report()
        break
    if st.get("guardian_recovery_attempted"):
        log("guardian_exit", reason="recovery already attempted")
        break
    st["guardian_recovery_attempted"] = True
    st["run_outcome"] = "runner_exited_unexpectedly"
    write_state(st)
    log("recovery_started", live_test_started=bool(st.get("live_test_started")))
    rows=process_rows()
    diag_pid=st.get("diagnostic_pid")
    if isinstance(diag_pid,int):
        if kill_if_exact(diag_pid, diag_name, rows): log("diagnostic_process_stopped", pid=diag_pid)
        else: log("diagnostic_process_not_stopped", reason="PID no longer matched diagnostic process")
    # Stop only recorded CPU-test workers whose command still matches the known stress-loop marker.
    for pid in st.get("active_stress_pids", []):
        if isinstance(pid,int):
            cmd=command_for(pid)
            if "while time.monotonic()<d" in cmd or "123456789" in cmd:
                try: os.kill(pid, signal.SIGTERM); log("stress_worker_stopped", pid=pid)
                except Exception: pass
    volume_ok = True
    jamzone_ok = True
    if st.get("live_test_started"):
        volume_ok = restore_volume()
        if volume_ok: st["live_test_started"] = False
    if st.get("jamzone_restore_needed") or st.get("loop_restore_needed"):
        loop_needed = bool(st.get("loop_restore_needed"))
        log_offset = st.get("loop_action_log_start")
        if not loop_needed and isinstance(log_offset, int):
            try:
                child_log = run_dir / "floating_bar_stdout_stderr.log"
                with child_log.open("rb") as f:
                    f.seek(max(0, log_offset)); chunk=f.read().decode("utf-8", errors="replace").lower()
                loop_needed = "[hid sent] key=l" in chunk
            except Exception: pass
        if loop_needed:
            loop_ok = key_jamzone("l")
            if loop_ok: st["loop_restore_needed"] = False
            else: jamzone_ok = False
        play_ok = key_jamzone("p")
        if play_ok:
            st["jamzone_restore_needed"] = False
        else: jamzone_ok = False
    rows=process_rows()
    v96=any(is_v96(cmd) for _,cmd in rows)
    v97=any(is_v97(cmd) for _,cmd in rows)
    # v16: do not restart v97 automatically. The untouched source file remains the manual rollback.
    log("controller_relaunch_skipped", reason="user authorized manual restart from preserved original v97 file")
    time.sleep(2)
    rows=process_rows()
    controller_present = any(is_v97(cmd) for _,cmd in rows)
    recovery_ok = bool(volume_ok and jamzone_ok and not st.get("live_test_started") and
                       not st.get("jamzone_restore_needed") and not st.get("loop_restore_needed"))
    st["terminal_cleanup_complete"] = recovery_ok
    st["run_outcome"] = "guardian_recovered_manual_v97_restart" if recovery_ok else "guardian_recovery_incomplete_manual_v97_restart"
    write_state(st)
    log("recovery_finished", test_resumed=False, controller_presence_verified=bool(controller_present),
        volume_restore_verified=bool(volume_ok), jamzone_restore_verified=bool(jamzone_ok), recovery_complete=recovery_ok,
        v97_relaunch="manual by user")
    # Recovery guardian is strictly recovery-only. Report packaging belongs solely
    # to the foreground runner after normal completion; this prevents a guardian
    # race from producing/updating an archive after abort or an unexpected exit.
    log("report_packaging_skipped", reason="guardian is recovery-only; no report archive is created by guardian")
    break
'''
    GUARDIAN_SCRIPT.write_text(guardian_source, encoding="utf-8")
    return GUARDIAN_SCRIPT


def start_guardian():
    global GUARDIAN_PROCESS
    script = create_guardian_script()
    env = minimal_environment()
    GUARDIAN_PROCESS = subprocess.Popen([sys.executable, str(script), str(os.getpid()), str(RUN_STATE),
                                         str(SOURCE), str(OUTDIR), EXPECTED_V97_SHA256],
                                        cwd=str(SCRIPT_DIR), env=env, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, start_new_session=True)
    update_run_state(guardian_pid=GUARDIAN_PROCESS.pid)
    note("Independent local recovery guardian started; it performs no network access and does not resume tests after a runner crash.")



def export_message_text_backup():
    """Export only active-row message text to a unique Desktop TXT; never export settings metadata."""
    settings_path = HOME / "Library" / "Application Support" / "MainStageJamZoneController" / "message_settings.json"
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = HOME / "Desktop" / f"Floating_Bar_Message_Text_Backup_{timestamp}.txt"
    if backup_path.exists():
        raise RuntimeError("Text-backup filename collision; refusing to overwrite an existing file.")

    source_label = "saved active message rows"
    if settings_path.is_file():
        try:
            with settings_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            raise RuntimeError(f"Could not safely read saved message text for backup ({type(exc).__name__}); no controller process was stopped.")
        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("Saved message settings have no valid rows list; refusing to make an incomplete backup or stop the controller.")
        if rows:
            texts = [str(row.get("text", "")) for row in rows if isinstance(row, dict)]
        else:
            source_label = "built-in default message rows (saved rows were empty; controller falls back to defaults)"
            texts = ["🎤 תציג עצמך 🎤", "🍷 לחיים 🥂", "📷 אינסטה 📷", "🍸 משקה הבית 🍹", "🇵🇹 🇮🇹 מאיפה אתם? 🇪🇸 🇫🇷"]
    else:
        source_label = "built-in default message rows (no saved settings file existed)"
        texts = ["🎤 תציג עצמך 🎤", "🍷 לחיים 🥂", "📷 אינסטה 📷", "🍸 משקה הבית 🍹", "🇵🇹 🇮🇹 מאיפה אתם? 🇪🇸 🇫🇷"]

    # The backup intentionally contains only message text in original row order.
    # No durations, visibility flags, images, presets, timer defaults, or other settings are exported.
    payload = "Floating Bar message text backup\n" + "Message text only; original row order preserved.\n\n"
    payload += "\n".join(texts) + "\n"
    try:
        with backup_path.open("x", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
    except Exception as exc:
        raise RuntimeError(f"Could not create the Desktop message-text backup ({type(exc).__name__}); no controller process was stopped.")
    log_json(ACTIONS_LOG, {"type": "message_text_backup_created", "filename": backup_path.name,
                           "source": source_label, "row_count": len(texts), "only_text_exported": True,
                           "settings_metadata_exported": False, "included_in_test_zip": False})
    note(f"Saved text-only message backup on Desktop: {backup_path.name} ({len(texts)} rows). No settings metadata was copied.")
    return backup_path


def main():
    write_runner_heartbeat("preflight")
    update_run_state(parent_pid=os.getpid(), swap_started=False, live_test_started=False,
                     diagnostic_pid=None, active_stress_pids=[], jamzone_restore_needed=False,
                     loop_restore_needed=False, loop_action_log_start=None,
                     terminal_cleanup_complete=False, normal_complete=False,
                     guardian_recovery_attempted=False, run_outcome="preflight")
    random.seed()
    note("PRECHECK: checking source, existing processes, apps, MIDI ports, and test permissions before stopping any process.")
    process_rows = get_process_rows()
    existing_diagnostic = [r["pid"] for r in process_rows if any(name in r["command"] for name in (
        "mainstage_jamzone_fade_controller_FLOATING_BAR_v96_NIGHTTEST.py",
        "mainstage_jamzone_fade_controller_FLOATING_BAR_v97_NIGHTTEST.py"))]
    existing_runner = [r["pid"] for r in process_rows if r["pid"] != os.getpid() and any(
        name in r["command"] for name in ("floating_bar_v96_night_test.py", "floating_bar_v96_night_test_v2.py",
        "floating_bar_v96_night_test_v3.py", "floating_bar_v96_night_test_v4.py", "floating_bar_v96_night_test_v5.py",
        "floating_bar_v97_night_test_v5.py", "floating_bar_v97_night_test_v6.py", "floating_bar_v97_night_test_v7.py",
        "floating_bar_v97_night_test_v8.py", "floating_bar_v97_night_test_v9.py", "floating_bar_v97_night_test_v10.py", "floating_bar_v97_night_test_v11.py", "floating_bar_v97_night_test_v12.py", "floating_bar_v97_night_test_v13.py", "floating_bar_v97_night_test_v16.py"))]
    if existing_diagnostic or existing_runner:
        raise RuntimeError(f"A diagnostic Floating Bar test/runner is already running (diagnostic PIDs={existing_diagnostic}, runner PIDs={existing_runner}); refusing to start a duplicate.")
    coyote_present = any("coyotemidi" in row.get("command", "").casefold().replace(" ", "") for row in process_rows)
    if not coyote_present:
        raise RuntimeError("CoyoteMIDI was not confirmed running. Open CoyoteMIDI and rerun; no controller process has been stopped.")
    note("CoyoteMIDI presence precheck passed; resource and presence monitoring will run every 10 seconds.")
    if not jamzone_exists():
        raise RuntimeError("JamZone is not running. Start JamZone and its loop manually, then rerun. Nothing was stopped.")
    rc_ms, out_ms, err_ms = apple_script('tell application "System Events" to (name of processes) contains "MainStage"')
    if rc_ms != 0 or out_ms.lower() != "true":
        raise RuntimeError("MainStage process was not confirmed running. Open MainStage and rerun. Nothing was stopped.")
    # SongBook Pro is required and must remain open. Use a direct process lookup
    # for the exact name shown in Activity Monitor, then conservative fallbacks.
    rc_sbp, out_sbp, err_sbp = apple_script('tell application "System Events" to exists (process "SongbookPro")')
    sbp_running = rc_sbp == 0 and out_sbp.strip().casefold() == "true"
    if not sbp_running:
        rc_sbp, out_sbp, err_sbp = apple_script('tell application "System Events" to get name of every process')
        sbp_names = [part.strip().strip('"').casefold() for part in out_sbp.split(",")] if rc_sbp == 0 else []
        sbp_running = any(name.replace(" ", "") == "songbookpro" for name in sbp_names)
    if not sbp_running:
        sbp_running = any("songbookpro" in row.get("command", "").casefold().replace(" ", "")
                          for row in process_rows)
    if not sbp_running:
        raise RuntimeError("SongbookPro was not confirmed running. Open SongbookPro and rerun. Nothing was stopped.")
    chrome_running = any("Google Chrome" in r["command"] for r in process_rows)
    spotify_running = any("Spotify" in r["command"] for r in process_rows)
    if chrome_running:
        raise RuntimeError("Chrome is still running. Quit Chrome with Cmd+Q, then rerun. Nothing was stopped.")
    if spotify_running:
        raise RuntimeError("A Spotify process is still running. Quit Spotify completely, then rerun. Nothing was stopped.")
    try:
        import Quartz
        from ApplicationServices import AXIsProcessTrusted
        accessibility_trusted = bool(AXIsProcessTrusted())
    except Exception as exc:
        raise RuntimeError(f"Quartz/ApplicationServices precheck failed ({type(exc).__name__}: {exc}); install/enable the required macOS Python bindings before testing. Nothing was stopped.")
    if not accessibility_trusted:
        raise RuntimeError("macOS Accessibility permission is not enabled for the Terminal/Python app running this test. Enable it in System Settings > Privacy & Security > Accessibility, then rerun. Nothing was stopped.")
    diag = make_diagnostic_copy()
    try:
        import mido
    except Exception as exc:
        raise RuntimeError(f"mido is unavailable in this Python environment: {exc}")
    bus1, testport, names = find_midi_output()
    if not bus1:
        raise RuntimeError(f"IAC Driver Bus 1 output not found. Available outputs: {names}. Nothing was stopped.")
    # The virtual port exists only after the diagnostic process starts; it will be resolved later.
    input_names = mido.get_input_names()
    output_names = mido.get_output_names()
    airstep_present = any("AIRSTEP Bluetooth" in n for n in input_names)
    smc_present = any("SMC-Mixer Bluetooth" in n for n in (input_names + output_names))
    bus2_present = any("IAC Driver Bus 2" in n for n in input_names)
    log_json(ACTIONS_LOG, {"type": "precheck_midi_ports", "input_port_count": len(input_names),
                           "output_port_count": len(output_names), "airstep_present": airstep_present,
                           "smc_present": smc_present, "bus2_present": bus2_present,
                           "chrome_running": chrome_running, "spotify_running": spotify_running,
                           "port_names_recorded": False})
    if not airstep_present:
        raise RuntimeError("AIRSTEP Bluetooth input not detected. Nothing was stopped.")
    if not smc_present:
        raise RuntimeError("SMC-Mixer Bluetooth port not detected in MIDI inputs/outputs. Nothing was stopped.")
    if not bus2_present:
        raise RuntimeError("IAC Driver Bus 2 input not detected. Nothing was stopped.")
    note("IAC Bus 1, AIRSTEP, SMC and IAC Bus 2 prechecks passed; port names are not stored in the report.")
    # Preserve only message text before the diagnostic copy starts. This is local and not included in the report ZIP.
    export_message_text_backup()
    note(f"Diagnostic copy created in the test-only runtime folder: {diag.name}")
    note(f"Original v97 SHA-256 at test start: {SOURCE_HASH_START[0]}")
    try:
        import platform
        env_info = {"runner_version": "v16", "start_local": dt.datetime.now().astimezone().isoformat(),
                    "python_version": sys.version, "platform": platform.platform(), "machine": platform.machine(),
                    "macos_version": run_capture(["sw_vers", "-productVersion"])[1],
                    "mido_version": getattr(mido, "__version__", "unknown"),
                    "source_file": SOURCE.name, "source_sha256": SOURCE_HASH_START[0],
                    "runner_file": Path(__file__).name, "diagnostic_copy_file": diag.name,
                    "privacy": {"network_access": "none; no network APIs or upload actions", "uploads": False,
                                "personal_documents_read": False, "only_saved_active_row_text_read_for_local_txt_backup": True,
                                "message_settings_metadata_exported": False, "message_text_backup_in_test_zip": False,
                                "midi_port_names_recorded": False, "unrelated_crash_reports_read_or_copied": False},
                    "test_duration_seconds": DURATION_SECONDS, "min_action_gap_seconds": MIN_ACTION_GAP,
                    "max_action_gap_seconds": MAX_ACTION_GAP}
        (OUTDIR / "environment.json").write_text(json.dumps(env_info, indent=2), encoding="utf-8")
    except Exception as exc:
        log_json(ACTIONS_LOG, {"type": "environment_capture_error", "error": f"{type(exc).__name__}: {exc}"})
    note("Test plan: 9 hours; randomized 60–150s gaps; Tk widget geometry and hit-area probes; G2M/TB, quick/loop/full fade, MainStage state toggles, volume ramps, timer tests, bounded 1/2/4-worker CPU stress, supervised restarts, restart-shortcut probe, internal/external freeze recovery, and mouse probes under stress. JamZone P/L restoration is foreground-verified. Privacy limits: no network calls/uploads; no personal documents; saved settings are read only to export active message text to a separate local TXT backup, with no durations/images/presets/timer settings exported; the TXT is not included in the report ZIP; MIDI port names and process command arguments are not recorded; no pre-existing Library logs or unrelated macOS crash reports are read. CoyoteMIDI presence, CPU, and memory are sampled every 10 seconds, with state changes logged. An independent local guardian stops test activity and restores volume/playback after an abrupt runner exit; it does not restart v97 or resume the test. The original v97 file remains available for manual restart.")
    write_runner_heartbeat("preflight_complete_before_controller_swap")
    start_guardian()
    update_run_state(swap_started=True, run_outcome="controller_swap_started")
    CONTROLLER_SWAP_STARTED[0] = True
    close_existing_controller()
    child = start_child()
    last_sample = 0.0
    last_top = 0.0
    last_restart_test = 0
    testport_name = None
    busout = None
    testout = None
    # Wait for virtual input to be advertised by CoreMIDI.
    for _ in range(30):
        if child.poll() is not None:
            raise RuntimeError(f"Diagnostic copy exited during startup; inspect {CHILD_LOG}")
        try:
            outputs = mido.get_output_names()
            testport_name = next((n for n in outputs if TEST_INPUT_NAME in n), None)
            if testport_name:
                testout = mido.open_output(testport_name)
                break
        except Exception:
            pass
        time.sleep(1)
    if testout is None:
        raise RuntimeError("Diagnostic virtual MIDI test port was not visible after 30 seconds. Aborting rather than running an incomplete test; automatic v97 relaunch is disabled; start the preserved original manually after cleanup.")
    # Confirm the diagnostic copy actually opened the physical AIRSTEP input and Bus 2 state listener.
    startup_deadline = time.monotonic() + 20
    startup_log = ""
    while time.monotonic() < startup_deadline:
        try: startup_log = CHILD_LOG.read_text(encoding="utf-8", errors="replace")
        except Exception: startup_log = ""
        airstep_opened = ("[BT MIDI RECOVERY] Opened MIDI input: AIRSTEP" in startup_log or
                          "[BT MIDI RECOVERY] Reopened MIDI input after error: AIRSTEP" in startup_log)
        bus2_opened = "MIDI diagnostic input: IAC Bus 2" in startup_log
        if airstep_opened and bus2_opened:
            break
        if child.poll() is not None:
            raise RuntimeError(f"Diagnostic copy exited before opening AIRSTEP/Bus 2. See {CHILD_LOG}")
        time.sleep(1)
    if not airstep_opened or not bus2_opened:
        raise RuntimeError(f"Diagnostic copy did not confirm opening AIRSTEP and IAC Bus 2 within 20 seconds (AIRSTEP={airstep_opened}, Bus2={bus2_opened}). Aborting; automatic v97 relaunch is disabled; start the preserved original manually after cleanup. See {CHILD_LOG}")
    try:
        startup_log = CHILD_LOG.read_text(encoding="utf-8", errors="replace")
    except Exception: startup_log = ""
    smc_receiver_opened = "[BT MIDI RECOVERY] Opened SMC state input." in startup_log
    log_json(ACTIONS_LOG, {"type": "startup_receiver_readiness", "airstep_opened": airstep_opened,
                           "bus2_opened": bus2_opened, "smc_receiver_opened": smc_receiver_opened,
                           "note": "SMC input readiness is reported separately; port presence is checked in preflight."})
    note("Startup verified: diagnostic copy opened AIRSTEP Bluetooth and IAC Driver Bus 2; virtual test MIDI port is available.")
    busout = mido.open_output(bus1)
    LIVE_TEST_STARTED[0] = True
    update_run_state(live_test_started=True)
    manual_mouse_baseline(child.pid)
    start_time = time.monotonic()
    end_time = start_time + DURATION_SECONDS
    note("Nine-hour observation clock started after startup readiness and manual mouse baseline.")

    # 10 repetitions each for mapped controls; 10 G2M repeats,
    # 10 TalkBack repeats, 10 master-volume ramps, 10 quick-volume ramps, 10 sidechain pairs,
    # 10 each valid status-control toggles (CC42/45/41/39/40; CC43 excluded because it is a Bus 2 state mirror), and 10 timer interaction probes. All actions are randomized.
    actions = []
    if testout is not None:
        actions += [("g2m_repeat", i) for i in range(1, 11)]
        actions += [("tb_repeat", i) for i in range(1, 11)]
        actions += [("quick_fade", i) for i in range(1, 11)]
        actions += [("loop_fade", i) for i in range(1, 11)]
        actions += [("full_fade", i) for i in range(1, 11)]
    for control in (42, 45, 41, 39, 40):
        actions += [("toggle_cc", (control, i)) for i in range(1, 11)]
    for note_num in (22, 18):
        actions += [("toggle_note", (note_num, i)) for i in range(1, 11)]
    actions += [("master_volume", i) for i in range(1, 11)]
    actions += [("quick_volume", i) for i in range(1, 11)]
    actions += [("effect_cc60", i) for i in range(1, 11)]
    actions += [("mouse_probe", i) for i in range(1, 11)]
    actions += [("cpu_stress", (workers, cycle)) for cycle, workers in enumerate((1, 2, 4), start=1)]
    actions += [("supervised_restart", i) for i in (1, 2)]
    actions += [("restart_shortcut_probe", 1)]
    actions += [("ui_freeze_internal", 1)]
    actions += [("ui_freeze_external", 1)]
    stop_scenarios = [a for a in actions if a[0] in ("quick_fade", "loop_fade", "full_fade")]
    mouse_scenarios = [a for a in actions if a[0] == "mouse_probe"]
    random.shuffle(mouse_scenarios)
    early_mouse = mouse_scenarios[:5]
    later_mouse = mouse_scenarios[5:]
    freeze_scenarios = [a for a in actions if a[0] in ("ui_freeze_internal", "ui_freeze_external")]
    shortcut_scenarios = [a for a in actions if a[0] == "restart_shortcut_probe"]
    soak_scenarios = [a for a in actions if a[0] not in ("quick_fade", "loop_fade", "full_fade", "mouse_probe", "ui_freeze_internal", "ui_freeze_external", "restart_shortcut_probe")]
    soak_scenarios += later_mouse + shortcut_scenarios
    random.shuffle(soak_scenarios)
    random.shuffle(stop_scenarios)
    # Freeze probes are deliberately ordered: first test the built-in watchdog, then the external supervisor.
    freeze_scenarios.sort(key=lambda a: 0 if a[0] == "ui_freeze_internal" else 1)
    # Establish a baseline click sample first; distribute remaining probes among normal and stress periods.
    actions = early_mouse + soak_scenarios + stop_scenarios + freeze_scenarios
    planned_actions = list(actions)
    planned_action_counts = {k: sum(1 for a in planned_actions if a[0] == k) for k in sorted(set(a[0] for a in planned_actions))}
    if any(kind in ("legacy_cc0_cancel", "legacy_cc57_cancel") for kind, _ in planned_actions):
        raise RuntimeError("Obsolete CC0/CC57 scenarios detected in the plan; refusing to begin live actions.")
    log_json(ACTIONS_LOG, {"type": "plan", "duration_seconds": DURATION_SECONDS, "action_count": len(planned_actions),
                          "action_types": {k: sum(1 for x in actions if x[0] == k) for k in sorted(set(x[0] for x in actions))},
                          "random_gap_seconds": [MIN_ACTION_GAP, MAX_ACTION_GAP], "test_input": testport_name})

    jamzone_restore_failed = False
    action_index = 0
    next_action_at = start_time + 15
    timer_phase_1_started = False
    timer_phase_1_stopped = False
    timer_phase_2_started = False
    timer_phase_2_stopped = False
    active_stress = None
    restart_cap_exhausted = False
    heartbeat_stale_seen = False
    heartbeat_recovery_pending = False
    last_timer_expired = None
    while time.monotonic() < end_time:
        write_runner_heartbeat("running")
        now = time.monotonic()
        if active_stress and now >= active_stress["end_at"]:
            stop_cpu_stress(active_stress, "scheduled 60-second load phase complete")
            active_stress = None
        elapsed = now - start_time
        if GUARDIAN_PROCESS is not None and GUARDIAN_PROCESS.poll() is not None and elapsed < DURATION_SECONDS:
            if GUARDIAN_RESTARTS[0] < MAX_GUARDIAN_RESTARTS:
                GUARDIAN_RESTARTS[0] += 1
                note(f"Independent recovery guardian exited unexpectedly; restarting guardian ({GUARDIAN_RESTARTS[0]}/{MAX_GUARDIAN_RESTARTS}).")
                try:
                    start_guardian()
                except Exception as _guardian_exc:
                    log_json(ACTIONS_LOG, {"type": "guardian_restart_error", "attempt": GUARDIAN_RESTARTS[0],
                                           "error_type": type(_guardian_exc).__name__})
            elif GUARDIAN_RESTARTS[0] == MAX_GUARDIAN_RESTARTS:
                GUARDIAN_RESTARTS[0] += 1
                note("Recovery guardian restart limit reached; continuing diagnostics without repeatedly spawning guardians.")
        # Exercise two long timer cycles with temporary diagnostic-only durations; saved defaults are never changed.
        if elapsed >= 30 * 60 and not timer_phase_1_started:
            note("Starting session timer phase 1 for 3h30m (diagnostic-only duration; saved default unchanged).")
            phase_ok = timer_phase_click("phase1_start_3h30_timer", duration_seconds=3*3600+30*60, bar_pid=child.pid)
            timer_phase_1_started = True
            if not phase_ok:
                timer_phase_1_stopped = True
                note("Phase 1 timer did not start/verify; timer phase 1 will be marked incomplete and will not be retried automatically.")
        if elapsed >= 3.5 * 60 * 60 and not timer_phase_1_stopped:
            note("Resetting phase 1 after three hours; expected ~30 minutes remained if timer stayed accurate.")
            timer_phase_click("phase1_reset_after_observation", reset_after=True, bar_pid=child.pid)
            timer_phase_1_stopped = True
        if elapsed >= 5 * 60 * 60 and not timer_phase_2_started:
            note("Starting session timer phase 2 for 2h30m (diagnostic-only duration; saved default unchanged).")
            phase_ok = timer_phase_click("phase2_start_2h30_timer", duration_seconds=2*3600+30*60, bar_pid=child.pid)
            timer_phase_2_started = True
            if not phase_ok:
                timer_phase_2_stopped = True
                note("Phase 2 timer did not start/verify; timer phase 2 will be marked incomplete and will not be retried automatically.")
        if elapsed >= 7 * 60 * 60 and not timer_phase_2_stopped:
            note("Resetting phase 2 after two hours; expected ~30 minutes remained if timer stayed accurate.")
            timer_phase_click("phase2_reset_after_observation", reset_after=True, bar_pid=child.pid)
            timer_phase_2_stopped = True
        if now - last_sample >= 10:
            sample_system(child)
            last_sample = now
        if now - last_top >= 60:
            # The 10-second resource samples already contain a sanitized top-18 process list.
            log_json(ACTIONS_LOG, {"type": "minute_resource_snapshot", "sample_interval_seconds": 10,
                                   "process_labels_only": True, "raw_top_output_not_collected": True})
            last_top = now
        hb = read_heartbeat()
        if hb:
            age = max(0.0, time.monotonic() - float(hb.get("monotonic", time.monotonic())))
            if age > INTERNAL_HEARTBEAT_WARNING_SECONDS and not heartbeat_stale_seen:
                heartbeat_stale_seen = True
                heartbeat_recovery_pending = True
                log_json(ACTIONS_LOG, {"type": "heartbeat_stale_observed", "age_seconds": round(age, 2), "pid": hb.get("pid"), "external_restart_threshold_seconds": HEARTBEAT_STALE_SECONDS})
            elif age <= 3 and heartbeat_stale_seen:
                heartbeat_stale_seen = False
                log_json(ACTIONS_LOG, {"type": "heartbeat_resumed_after_stall", "age_seconds": round(age, 2), "pid": hb.get("pid")})
                if heartbeat_recovery_pending and child.poll() is None:
                    testout, reopened_name = reopen_test_output(testout, timeout=20)
                    testport_name = reopened_name
                    heartbeat_recovery_pending = False
                    log_json(ACTIONS_LOG, {"type": "test_input_reopened_after_heartbeat_recovery", "virtual_port": reopened_name, "success": testout is not None})
                    if testout is None: actions = []
            expired_now = hb.get("timer_expired")
            if expired_now != last_timer_expired:
                log_json(ACTIONS_LOG, {"type": "timer_expired_state_change", "expired": expired_now, "remaining_seconds": hb.get("timer_remaining_seconds"), "pid": hb.get("pid")})
                last_timer_expired = expired_now
        if child.poll() is not None:
            if restart_cap_exhausted:
                time.sleep(2)
                continue
            note(f"Diagnostic process exited with code {child.returncode}.")
            try: child._night_log_handle.close()
            except Exception: pass
            if supervisor_restarts[0] < MAX_SUPERVISOR_RESTARTS:
                supervisor_restarts[0] += 1
                try:
                    if testout is not None: testout.close()
                except Exception: pass
                testout = None
                child = start_child()
                testport_name, airstep_ok, bus2_ok = wait_for_diagnostic_ready(child, timeout=30)
                if testport_name and airstep_ok and bus2_ok:
                    try: testout = mido.open_output(testport_name)
                    except Exception as exc: testout = None; log_json(ACTIONS_LOG, {"type": "virtual_port_reopen_error", "error": str(exc)})
                else:
                    testout = None; actions = []
                    log_json(ACTIONS_LOG, {"type": "restart_recovery_failed", "reason": "diagnostic child did not restore virtual port plus AIRSTEP and Bus2", "virtual_port": testport_name, "airstep_opened": airstep_ok, "bus2_opened": bus2_ok})
            else:
                note("Restart cap reached; stopping active tests but continuing system monitoring.")
                actions = []
                restart_cap_exhausted = True
        elif ((hb and time.monotonic() - float(hb.get("monotonic", time.monotonic())) > HEARTBEAT_STALE_SECONDS)
              or (hb is None and time.monotonic() - getattr(child, "_night_started_at", time.monotonic()) > 25)):
            stale_age = (time.monotonic() - float(hb.get("monotonic", time.monotonic()))) if hb else (time.monotonic() - getattr(child, "_night_started_at", time.monotonic()))
            note(f"UI heartbeat missing/stale for {stale_age:.1f}s; external supervisor restarting diagnostic process.")
            stop_child(child, "UI heartbeat stale")
            supervisor_restarts[0] += 1
            if supervisor_restarts[0] <= MAX_SUPERVISOR_RESTARTS:
                try:
                    if testout is not None: testout.close()
                except Exception: pass
                testout = None
                child = start_child()
                testport_name, airstep_ok, bus2_ok = wait_for_diagnostic_ready(child, timeout=30)
                if testport_name and airstep_ok and bus2_ok:
                    try: testout = mido.open_output(testport_name)
                    except Exception as exc: testout = None; log_json(ACTIONS_LOG, {"type": "virtual_port_reopen_error", "error": str(exc)})
                else:
                    testout = None; actions = []
                    log_json(ACTIONS_LOG, {"type": "restart_recovery_failed", "reason": "diagnostic child did not restore virtual port plus AIRSTEP and Bus2", "virtual_port": testport_name, "airstep_opened": airstep_ok, "bus2_opened": bus2_ok})
            else:
                note("Restart cap reached after UI freeze; no further process restarts will be attempted.")
                actions = []
                restart_cap_exhausted = True
        if action_index < len(actions) and now >= next_action_at:
            kind, val = actions[action_index]
            action_index += 1
            action_succeeded = False
            action_error_occurred = False
            # Never count a required MIDI scenario as executed if its virtual test port is unavailable.
            if kind in ("g2m_repeat", "tb_repeat", "quick_fade", "loop_fade", "full_fade") and testout is None:
                log_json(ACTIONS_LOG, {"type": "action_skipped", "action": kind, "cycle": val, "reason": "test MIDI port unavailable"})
                next_action_at = time.monotonic() + random.randint(MIN_ACTION_GAP, MAX_ACTION_GAP)
                time.sleep(2)
                continue
            try:
                if kind == "g2m_repeat" and testout:
                    action_succeeded = action_g2m_repeat(testout, val)
                elif kind == "tb_repeat" and testout:
                    action_succeeded = action_tb_repeat(testout, val)
                elif kind in ("quick_fade", "loop_fade", "full_fade") and testout:
                    if jamzone_restore_failed:
                        log_json(ACTIONS_LOG, {"type": "action_skipped", "action": kind, "cycle": val,
                                               "reason": "previous HID P/L dispatch failed; stop/playback tests paused"})
                    else:
                        if kind == "quick_fade": restored = action_quick_fade(testout, val)
                        elif kind == "loop_fade": restored = action_loop_fade(testout, val, child.pid)
                        else: restored = action_full_fade(testout, val)
                        if restored:
                            action_succeeded = True
                        else:
                            jamzone_restore_failed = True
                            note(f"{kind} repetitions paused: HID P/L dispatch or JamZone activation failed. No further automatic playback-stop scenarios will be attempted.")
                elif kind == "toggle_cc":
                    control, cycle = val
                    feedback = action_midi_toggle(busout, "cc", control)
                    log_json(ACTIONS_LOG, {"type": "mainstage_toggle_cc", "cc": control, "cycle": cycle, "channel": 15, "paired_pulses": True, "explicit_release_values": True, **feedback})
                    action_succeeded = bool(feedback.get("bus2_feedback_complete"))
                elif kind == "toggle_note":
                    note_num, cycle = val
                    feedback = action_midi_toggle(busout, "note", note_num)
                    log_json(ACTIONS_LOG, {"type": "mainstage_toggle_note", "note": note_num, "cycle": cycle, "channel": 15, "paired_pulses": True, "explicit_release_values": True, **feedback})
                    action_succeeded = bool(feedback.get("bus2_feedback_complete"))
                elif kind == "master_volume":
                    action_volume(busout, 20, val); action_succeeded = True
                elif kind == "quick_volume":
                    action_volume(busout, 21, val); action_succeeded = True
                elif kind == "effect_cc60":
                    try: before_offset = BUS2_LOG.stat().st_size
                    except OSError: before_offset = 0
                    send_cc(busout, 14, 60, 0); time.sleep(7.1); send_cc(busout, 14, 60, 0)
                    time.sleep(0.8)
                    feedback_count = bus2_feedback_count_since(before_offset, "cc", 43)
                    log_json(ACTIONS_LOG, {"type": "sidechain_action_pair", "cc": 60, "cycle": val,
                                           "bus2_cc43_feedback_count": feedback_count,
                                           "bus2_feedback_seen": feedback_count > 0,
                                           "bus2_feedback_minimum_expected": 2,
                                           "bus2_feedback_complete": feedback_count >= 2})
                    action_succeeded = feedback_count >= 2
                elif kind == "mouse_probe":
                    action_succeeded = mouse_probe(val, bar_pid=child.pid)
                elif kind == "cpu_stress":
                    workers, cycle = val
                    if active_stress:
                        stop_cpu_stress(active_stress, "replaced by next stress phase")
                    active_stress = start_cpu_stress(workers, cycle)
                    time.sleep(5)
                    mouse_probe(f"during_{workers}_worker_stress", bar_pid=child.pid)
                    log_json(ACTIONS_LOG, {"type": "mouse_probe_during_cpu_stress", "workers": workers, "cycle": cycle})
                    action_succeeded = True
                elif kind == "ui_freeze_internal":
                    try:
                        write_json_atomic(FREEZE_REQUEST, {"seconds": 10, "request_id": f"internal-{time.time_ns()}"})
                        log_json(ACTIONS_LOG, {"type": "ui_freeze_internal_requested", "seconds": 10,
                                               "expected_recovery": "internal watchdog via process exec/restart before external 15-second threshold", "diagnostic_only": True})
                        action_succeeded = True
                    except Exception as exc:
                        log_json(ACTIONS_LOG, {"type": "ui_freeze_internal_error", "error": f"{type(exc).__name__}: {exc}"})
                elif kind == "ui_freeze_external":
                    note("Preparing external-only freeze test: restarting diagnostic copy with its internal watchdog disabled only for this probe.")
                    try:
                        if testout is not None: testout.close()
                    except Exception: pass
                    testout = None
                    stop_child(child, "prepare external-supervisor-only freeze probe")
                    child = start_child(disable_internal_watchdog=True)
                    testport_name, airstep_ok, bus2_ok = wait_for_diagnostic_ready(child, timeout=30)
                    if testport_name and airstep_ok and bus2_ok:
                        try: testout = mido.open_output(testport_name)
                        except Exception: testout = None
                    else:
                        testout = None
                        actions = []
                        log_json(ACTIONS_LOG, {"type": "external_freeze_probe_skipped", "reason": "diagnostic startup failed", "virtual_port": testport_name, "airstep_opened": airstep_ok, "bus2_opened": bus2_ok})
                    if testout is not None:
                        try:
                            _startup_chunk = CHILD_LOG.read_text(encoding="utf-8", errors="replace")
                            watchdog_disabled_confirmed = "Internal watchdog disabled for external-supervisor-only freeze probe." in _startup_chunk
                        except Exception:
                            watchdog_disabled_confirmed = False
                        if watchdog_disabled_confirmed:
                            write_json_atomic(FREEZE_REQUEST, {"seconds": 25, "request_id": f"external-{time.time_ns()}"})
                            log_json(ACTIONS_LOG, {"type": "ui_freeze_external_requested", "seconds": 25,
                                                   "expected_external_detection_seconds": HEARTBEAT_STALE_SECONDS,
                                                   "internal_watchdog_disabled_for_this_child_only": True, "diagnostic_only": True})
                            action_succeeded = True
                        else:
                            actions = []
                            log_json(ACTIONS_LOG, {"type": "external_freeze_probe_skipped", "reason": "internal watchdog disable not confirmed in child log; no freeze induced"})
                elif kind == "restart_shortcut_probe":
                    before_size = CHILD_LOG.stat().st_size if CHILD_LOG.exists() else 0
                    front_ok = bring_process_frontmost(child.pid)
                    time.sleep(0.3)
                    if not front_ok:
                        log_json(ACTIONS_LOG, {"type": "restart_shortcut_probe", "result": "skipped", "reason": "could not confirm bar foreground; § not sent"})
                        actions = []
                        next_action_at = time.monotonic() + random.randint(MIN_ACTION_GAP, MAX_ACTION_GAP)
                        time.sleep(2)
                        continue
                    rc, out, err = send_keycode(10)  # ISO physical § key expected by v97
                    deadline = time.monotonic() + 18
                    restart_marker_seen = False
                    while time.monotonic() < deadline:
                        try:
                            with CHILD_LOG.open("r", encoding="utf-8", errors="replace") as _f:
                                _f.seek(before_size)
                                chunk = _f.read()
                            if "FULL PROCESS RESTART COMPLETED" in chunk or "[RESTART] Full process restart completed successfully." in chunk:
                                restart_marker_seen = True; break
                        except Exception: pass
                        if child.poll() is not None: break
                        time.sleep(0.5)
                    if restart_marker_seen:
                        testout, reopened_name = reopen_test_output(testout, timeout=20)
                        testport_name = reopened_name
                    log_json(ACTIONS_LOG, {"type": "restart_shortcut_probe", "physical_keycode": 10,
                                           "send_rc": rc, "send_stdout": out, "send_stderr": err,
                                           "restart_marker_seen": restart_marker_seen,
                                           "virtual_port_reopened": testout is not None,
                                           "note": "A successful AppleScript key dispatch alone does not prove the controller restart; restart marker and virtual-port recovery are checked."})
                    if restart_marker_seen and testout is not None: action_succeeded = True
                    else: actions = []
                elif kind == "supervised_restart":
                    note(f"Controlled supervisor restart probe {val}/2: stopping and relaunching only the diagnostic Floating Bar process.")
                    old_pid = child.pid
                    try:
                        if testout is not None: testout.close()
                    except Exception: pass
                    testout = None
                    stop_child(child, f"controlled restart probe {val}/2")
                    time.sleep(1.0)
                    child = start_child()
                    testport_name, airstep_ok, bus2_ok = wait_for_diagnostic_ready(child, timeout=30)
                    try: testout = mido.open_output(testport_name) if testport_name and airstep_ok and bus2_ok else None
                    except Exception: testout = None
                    log_json(ACTIONS_LOG, {"type": "supervised_restart_probe", "cycle": val, "old_pid": old_pid,
                                           "new_pid": child.pid, "new_process_alive": child.poll() is None,
                                           "virtual_port_found": bool(testport_name), "virtual_port_name": testport_name,
                                           "airstep_recovered": airstep_ok, "bus2_recovered": bus2_ok,
                                           "test_input_reopened": testout is not None})
                    if testout is not None: action_succeeded = True
                    else: actions = []
                if action_succeeded:
                    EXECUTED_ACTION_COUNTS[kind] = EXECUTED_ACTION_COUNTS.get(kind, 0) + 1
                    log_json(ACTIONS_LOG, {"type": "action_complete", "action": kind, "index": action_index, "planned_total": len(planned_actions)})
                elif not action_error_occurred:
                    log_json(ACTIONS_LOG, {"type": "action_skipped", "action": kind, "index": action_index, "reason": "action did not meet its success criteria"})
            except Exception as exc:
                action_error_occurred = True
                ACTION_ERROR_COUNT[0] += 1
                log_json(ACTIONS_LOG, {"type": "action_error", "action": kind, "index": action_index, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()})
            if jamzone_restore_failed and kind in ("quick_fade", "loop_fade", "full_fade"):
                next_action_at = time.monotonic() + 1
            else:
                next_action_at = time.monotonic() + random.randint(MIN_ACTION_GAP, MAX_ACTION_GAP)
        time.sleep(2)

    # End: stop stress, reset timer if possible, stop the diagnostic process, then restore volume.
    write_runner_heartbeat("finalizing")
    if active_stress:
        stop_cpu_stress(active_stress, "nine-hour test ending")
    try:
        if bring_process_frontmost(child.pid):
            timer_reset = click_widget_center("timer_reset_button")
            time.sleep(0.5)
            activate_jamzone()
        else:
            timer_reset = False
        log_json(ACTIONS_LOG, {"type": "final_timer_reset", "click_injected": timer_reset,
                               "timer_running_after": (read_heartbeat() or {}).get("timer_running")})
    except Exception as exc:
        log_json(ACTIONS_LOG, {"type": "final_timer_reset_error", "error": str(exc)})
    if RUN_STATE_DATA.get("jamzone_restore_needed") or RUN_STATE_DATA.get("loop_restore_needed"):
        try:
            loop_needed = bool(RUN_STATE_DATA.get("loop_restore_needed"))
            log_offset = RUN_STATE_DATA.get("loop_action_log_start")
            if not loop_needed and isinstance(log_offset, int):
                try:
                    with CHILD_LOG.open("rb") as _logf:
                        _logf.seek(max(0, log_offset))
                        loop_needed = "[hid sent] key=l" in _logf.read().decode("utf-8", errors="replace").lower()
                except Exception: pass
            loop_ok = send_jamzone_hid_key("l") if loop_needed else True
            play_ok = send_jamzone_hid_key("p")
            log_json(ACTIONS_LOG, {"type": "final_jamzone_restore", "loop_restore_needed": loop_needed,
                                   "loop_restore_dispatch_ok": loop_ok, "playback_restore_dispatch_ok": play_ok})
        except Exception as exc:
            log_json(ACTIONS_LOG, {"type": "final_jamzone_restore_error", "error": f"{type(exc).__name__}: {exc}"})
    stop_child(child, "scheduled nine-hour test complete")
    try:
        send_cc(busout, 14, 20, 127)
        send_cc(busout, 14, 21, 127)
        LIVE_TEST_STARTED[0] = False
        update_run_state(live_test_started=False, jamzone_restore_needed=False, loop_restore_needed=False,
                         active_stress_pids=[], diagnostic_pid=None)
        log_json(ACTIONS_LOG, {"type": "final_volume_restore", "cc20": 127, "cc21": 127, "sent_after_diagnostic_shutdown": True})
    except Exception as exc:
        log_json(ACTIONS_LOG, {"type": "final_volume_restore_error", "error": str(exc)})
    try:
        if testout: testout.close()
        if busout: busout.close()
    except Exception: pass
    try:
        crash_bytes = CRASH_LOG.stat().st_size if CRASH_LOG.is_file() else 0
        log_json(ACTIONS_LOG, {"type": "test_only_crash_log_summary", "exists": CRASH_LOG.is_file(),
                               "bytes": crash_bytes, "unrelated_crash_reports_read_or_copied": False})
        note("Privacy: no pre-existing Library logs or unrelated macOS crash reports were read or copied.")
    except Exception as exc:
        note(f"Could not inspect the test-only crash log metadata: {type(exc).__name__}: {exc}")
    # v16: automatic v97 relaunch intentionally disabled per user instruction.
    v97_pids = []
    try:
        process_rows_after = get_process_rows()
        v97_pids = [r["pid"] for r in process_rows_after if is_controller_row(r, "v97")]
        note("v16: automatic v97 relaunch skipped by user authorization. The original v97 file was not modified; restart it manually after the test if needed.")
    except Exception as exc:
        note(f"Could not inspect controller process state: {type(exc).__name__}: {exc}")
    import hashlib
    final_source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest() if SOURCE.is_file() else None
    source_unchanged = bool(SOURCE_HASH_START[0] and final_source_hash == SOURCE_HASH_START[0])
    cleanup_complete = bool(not LIVE_TEST_STARTED[0] and not RUN_STATE_DATA.get("jamzone_restore_needed") and
                            not RUN_STATE_DATA.get("loop_restore_needed"))
    run_complete = bool(cleanup_complete and source_unchanged)
    update_run_state(terminal_cleanup_complete=cleanup_complete, normal_complete=run_complete,
                     run_outcome="completed_manual_v97_restart" if run_complete else "cleanup_incomplete_manual_v97_restart")
    planned_counts = planned_action_counts
    missing_counts = {k: max(0, count - EXECUTED_ACTION_COUNTS.get(k, 0)) for k, count in planned_counts.items()
                      if EXECUTED_ACTION_COUNTS.get(k, 0) < count}
    log_json(ACTIONS_LOG, {"type": "final_completeness", "planned_action_counts": planned_counts,
                           "executed_action_counts": dict(EXECUTED_ACTION_COUNTS), "missing_action_counts": missing_counts,
                           "action_error_count": ACTION_ERROR_COUNT[0], "guardian_restart_count": min(GUARDIAN_RESTARTS[0], MAX_GUARDIAN_RESTARTS),
                           "source_hash_start": SOURCE_HASH_START[0],
                           "source_hash_end": final_source_hash, "v97_source_unchanged": source_unchanged, "automatic_v97_relaunch": False,
                           "elapsed_seconds": round(time.monotonic() - start_time, 2),
                           "scheduled_duration_seconds": DURATION_SECONDS})
    if not source_unchanged:
        note("CRITICAL WARNING: v97 source hash changed during test; investigate before using the controller.")
    try:
        summary = {"generated_at": dt.datetime.now().astimezone().isoformat(),
                   "scheduled_duration_seconds": DURATION_SECONDS,
                   "elapsed_seconds": round(time.monotonic() - start_time, 2),
                   "planned_action_counts": planned_counts,
                   "executed_action_counts": dict(EXECUTED_ACTION_COUNTS),
                   "missing_action_counts": missing_counts,
                   "action_error_count": ACTION_ERROR_COUNT[0],
                   "guardian_restart_count": min(GUARDIAN_RESTARTS[0], MAX_GUARDIAN_RESTARTS),
                   "source_sha256_start": SOURCE_HASH_START[0],
                   "source_sha256_end": final_source_hash,
                   "v97_source_unchanged": source_unchanged, "automatic_v97_relaunch": False, "automatic_v97_relaunch": False,
                   "resource_processes": {}, "chayotemidi": {"samples_present": 0, "samples_absent": 0,
                                                        "cpu_peak": 0.0, "rss_peak_kb": 0},
                   "mouse": {}, "midi": {}, "bus2_message_count": 0,
                   "privacy": {"network_access": "none", "uploads": False, "personal_documents_read": False,
                               "only_saved_active_row_text_read_for_local_txt_backup": True,
                               "message_settings_metadata_exported": False, "message_text_backup_in_test_zip": False,
                               "midi_port_names_recorded": False, "preexisting_library_logs_read": False,
                               "unrelated_crash_reports_read_or_copied": False,
                               "process_command_arguments_logged": False, "report_contains_diagnostic_code": False}}
        process_acc = {}
        chayotemidi_summary = {"samples_present": 0, "samples_absent": 0,
                               "cpu_peak": 0.0, "rss_peak_kb": 0}
        if SYSTEM_LOG.is_file():
            with SYSTEM_LOG.open("r", encoding="utf-8") as _sf:
                for _line in _sf:
                    try: _sample = json.loads(_line)
                    except Exception: continue
                    _chayo_rows = list(_sample.get("chayotemidi_processes", []))
                    if _chayo_rows:
                        chayotemidi_summary["samples_present"] += 1
                        chayotemidi_summary["cpu_peak"] = max(chayotemidi_summary["cpu_peak"],
                            max(float(_r.get("cpu_pct", 0.0)) for _r in _chayo_rows))
                        chayotemidi_summary["rss_peak_kb"] = max(chayotemidi_summary["rss_peak_kb"],
                            max(int(_r.get("rss_kb", 0)) for _r in _chayo_rows))
                    else:
                        chayotemidi_summary["samples_absent"] += 1
                    _rows = list(_sample.get("top_processes", []))
                    _rows.extend(_chayo_rows)
                    _child_row = _sample.get("child")
                    if _child_row and not any(_r.get("pid") == _child_row.get("pid") for _r in _rows):
                        _rows.append(_child_row)
                    for _row in _rows:
                        _name = _row.get("process_name", "unknown")
                        _acc = process_acc.setdefault(_name, {"samples": 0, "cpu_sum": 0.0, "cpu_peak": 0.0, "rss_peak_kb": 0})
                        _cpu = float(_row.get("cpu_pct", 0.0))
                        _acc["samples"] += 1; _acc["cpu_sum"] += _cpu
                        _acc["cpu_peak"] = max(_acc["cpu_peak"], _cpu)
                        _acc["rss_peak_kb"] = max(_acc["rss_peak_kb"], int(_row.get("rss_kb", 0)))
        for _name, _acc in process_acc.items():
            _acc["cpu_average_when_sampled"] = round(_acc.pop("cpu_sum") / max(1, _acc["samples"]), 2)
        summary["resource_processes"] = dict(sorted(process_acc.items(), key=lambda kv: kv[1]["cpu_peak"], reverse=True)[:30])
        summary["chayotemidi"] = chayotemidi_summary
        mouse_counts = {"injected_clicks": 0, "tk_events": 0, "ui_tick_lag_events": 0, "max_ui_tick_lag_seconds": 0.0, "events_by_widget": {}}
        if CLICK_LOG.is_file():
            with CLICK_LOG.open("r", encoding="utf-8") as _mf:
                for _line in _mf:
                    try: _event = json.loads(_line)
                    except Exception: continue
                    if _event.get("type") == "INJECTED_CLICK": mouse_counts["injected_clicks"] += 1
                    elif _event.get("type") == "UI_TICK_LAG":
                        mouse_counts["ui_tick_lag_events"] += 1
                        mouse_counts["max_ui_tick_lag_seconds"] = max(mouse_counts["max_ui_tick_lag_seconds"], float(_event.get("lag_seconds", 0)))
                    elif "event_type" in _event:
                        mouse_counts["tk_events"] += 1
                        _widget = _event.get("widget", "unknown")
                        mouse_counts["events_by_widget"][_widget] = mouse_counts["events_by_widget"].get(_widget, 0) + 1
        mouse_counts["events_by_widget"] = dict(sorted(mouse_counts["events_by_widget"].items(), key=lambda kv: kv[1], reverse=True)[:50])
        try:
            _all_mouse_records = []
            with CLICK_LOG.open("r", encoding="utf-8") as _mf:
                for _line in _mf:
                    try: _all_mouse_records.append(json.loads(_line))
                    except Exception: pass
            mouse_counts["delivery_analysis"] = click_delivery_metrics(_all_mouse_records)
        except Exception as _mouse_exc:
            mouse_counts["delivery_analysis_error"] = type(_mouse_exc).__name__
        summary["mouse"] = mouse_counts
        midi_counts = {"logged_records": 0, "cc11_aggregate_max_count": 0, "by_source": {}, "by_type": {}, "by_channel_control": {}}
        if MIDI_LOG.is_file():
            with MIDI_LOG.open("r", encoding="utf-8") as _mf:
                for _line in _mf:
                    try: _event = json.loads(_line)
                    except Exception: continue
                    midi_counts["logged_records"] += 1
                    if _event.get("type") == "cc11_aggregate_sample":
                        midi_counts["cc11_aggregate_max_count"] = max(midi_counts["cc11_aggregate_max_count"], int(_event.get("cc11_count", 0)))
                    _source = _event.get("source", "unknown")
                    midi_counts["by_source"][_source] = midi_counts["by_source"].get(_source, 0) + 1
                    _type = _event.get("type", "unknown")
                    midi_counts["by_type"][_type] = midi_counts["by_type"].get(_type, 0) + 1
                    if _type == "cc11_aggregate_sample":
                        _key = "CC11 aggregate sample"
                    else:
                        _key = f"ch{_event.get('midi_channel_1_based', _event.get('channel', '?'))}:cc{_event.get('control')}" if _event.get("control") is not None else f"ch{_event.get('midi_channel_1_based', _event.get('channel', '?'))}:{_type}"
                    midi_counts["by_channel_control"][_key] = midi_counts["by_channel_control"].get(_key, 0) + 1
        summary["midi"] = midi_counts
        if BUS2_LOG.is_file():
            with BUS2_LOG.open("r", encoding="utf-8") as _bf:
                summary["bus2_message_count"] = sum(1 for _line in _bf if _line.strip())
        summary["unrelated_crash_reports_read_or_copied"] = False
        (OUTDIR / "report_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        note(f"Could not build report_summary.json: {type(exc).__name__}: {exc}")
    sanitize_generated_text_files()
    import zipfile
    zip_path = OUTDIR.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in OUTDIR.rglob("*"):
            if not file.is_file():
                continue
            rel = file.relative_to(OUTDIR)
            if rel.parts and rel.parts[0] == "runtime":
                continue
            if file.name == "test_message_settings.json":
                continue
            if file.name.endswith(".tmp"):
                continue
            zf.write(file, file.relative_to(OUTDIR.parent))
    note(f"NINE-HOUR TEST FINISHED. Report ZIP: {zip_path}")
    note("v16: original v97 was not automatically restarted; launch the preserved original manually after the test if you want the bar running.")
    note("The ZIP is saved locally only; nothing is uploaded. It contains sanitized resource/process labels, MIDI/UI event metadata, and test-generated controller diagnostics.")


supervisor_restarts = [0]

if __name__ == "__main__":
    import atexit
    atexit.register(emergency_cleanup)
    def _handle_sigterm(_signum, _frame):
        raise KeyboardInterrupt("Stop requested; running emergency cleanup")
    signal.signal(signal.SIGTERM, _handle_sigterm)
    # Global Escape listener: requests controlled interruption; emergency cleanup
    # still stops stress workers and restores playback/volume where possible; v97 restart is manual.
    try:
        from pynput import keyboard as _pynput_keyboard
        def _on_key_press(key):
            if key == _pynput_keyboard.Key.esc:
                ESC_ABORT_REQUESTED[0] = True
                try: update_run_state(abort_requested=True, run_outcome="user_abort_requested")
                except Exception: pass
                try: note("ESC PRESSED: abort requested; no new test actions will be intentionally scheduled; safety cleanup begins.")
                except Exception: pass
                try: os.kill(os.getpid(), signal.SIGTERM)
                except Exception: pass
                return False
        _esc_listener = _pynput_keyboard.Listener(on_press=_on_key_press)
        _esc_listener.daemon = True
        _esc_listener.start()
        print("[NIGHT TEST] Press ESC at any time to abort and run safety cleanup. No partial report ZIP will be created; minimal local recovery logs may remain.", flush=True)
    except Exception as _esc_exc:
        note(f"FATAL: ESC abort listener could not start: {type(_esc_exc).__name__}; refusing to start test.")
        raise RuntimeError("ESC abort listener unavailable; test refused for safety.")
    try:
        main()
    except KeyboardInterrupt:
        note("Interrupted by user; report files retained.")
        sys.exit(130)
    except Exception as exc:
        note(f"FATAL PRECHECK/TEST ERROR: {type(exc).__name__}: {exc}")
        note(traceback.format_exc())
        sys.exit(1)
