"""Isolated CoreMIDI routing feasibility probe; never opens physical MIDI ports."""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import resource
import signal
import time

import rtmidi


INPUT_NAME = "FloBar Feasibility Router Input"
OUTPUT_NAME = "FloBar Feasibility Router Output"
WATCHDOG_SECONDS = 0.35
TEST_CC = 119
REPLACEMENT_CC = 118


def ui_heartbeat(heartbeat):
    while True:
        heartbeat.value = time.monotonic()
        time.sleep(0.05)


def wait_for_port(port_getter, name, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ports = port_getter()
        if name in ports:
            return ports.index(name)
        time.sleep(0.05)
    raise TimeoutError(f"CoreMIDI did not publish test endpoint: {name}")


def main():
    ctx = mp.get_context("spawn")
    heartbeat = ctx.Value("d", time.monotonic(), lock=False)
    mode = ctx.Value("i", 0, lock=False)  # 0=pass through, 1=replace CC119 with CC118
    received = queue.Queue()
    input_times = {}
    latency_ms = []

    router_in = rtmidi.MidiIn()
    router_out = rtmidi.MidiOut()
    sender = rtmidi.MidiOut()
    observer = rtmidi.MidiIn()
    ui = None

    def route_callback(message, _delta):
        data = list(message[0])
        if len(data) == 3 and data[0] & 0xF0 == 0xB0 and data[1] == TEST_CC:
            input_times[data[2]] = time.monotonic()
            ui_fresh = time.monotonic() - heartbeat.value <= WATCHDOG_SECONDS
            effective_mode = mode.value if ui_fresh else 0
            if effective_mode == 1:
                data[1] = REPLACEMENT_CC
        router_out.send_message(data)

    def observe_callback(message, _delta):
        data = tuple(message[0])
        now = time.monotonic()
        if len(data) == 3 and data[0] & 0xF0 == 0xB0:
            started = input_times.get(data[2])
            if started is not None:
                latency_ms.append((now - started) * 1000.0)
        received.put((data, now))

    try:
        router_in.open_virtual_port(INPUT_NAME)
        router_out.open_virtual_port(OUTPUT_NAME)
        router_in.set_callback(route_callback)

        observer.ignore_types(sysex=True, timing=True, active_sense=True)
        observer_index = wait_for_port(observer.get_ports, OUTPUT_NAME)
        observer.open_port(observer_index)
        observer.set_callback(observe_callback)

        sender_index = wait_for_port(sender.get_ports, INPUT_NAME)
        sender.open_port(sender_index)

        ui = ctx.Process(target=ui_heartbeat, args=(heartbeat,), name="FloBarProbeUI")
        ui.start()
        time.sleep(0.15)

        def check_one(label, value, expected_control, current_mode):
            mode.value = current_mode
            time.sleep(0.05)
            while True:
                try:
                    received.get_nowait()
                except queue.Empty:
                    break
            sender.send_message([0xB0, TEST_CC, value])  # channel 1, CC119 only
            first = received.get(timeout=2.0)[0]
            time.sleep(0.12)
            extras = []
            while True:
                try:
                    extras.append(received.get_nowait()[0])
                except queue.Empty:
                    break
            expected = (0xB0, expected_control, value)
            if first != expected or extras:
                raise AssertionError(
                    f"{label}: expected exactly [{expected}], got {[first, *extras]}"
                )
            print(f"{label}: PASS; one event {list(first)}; no duplicate")

        check_one("Original passthrough", 37, TEST_CC, 0)
        check_one("Replacement", 38, REPLACEMENT_CC, 1)

        # Simulate a crashed UI process. The independent router should time out
        # its heartbeat and fall back to the original message.
        ui.terminate()
        ui.join(timeout=2.0)
        time.sleep(WATCHDOG_SECONDS + 0.1)
        check_one("UI process crash -> original passthrough", 39, TEST_CC, 1)

        # Simulate a hung UI process without killing it. The router must make
        # the same fallback decision when the heartbeat stops advancing.
        ui = ctx.Process(target=ui_heartbeat, args=(heartbeat,), name="FloBarProbeUIHang")
        ui.start()
        time.sleep(0.15)
        mode.value = 1
        os.kill(ui.pid, signal.SIGSTOP)
        time.sleep(WATCHDOG_SECONDS + 0.1)
        check_one("UI process hang -> original passthrough", 40, TEST_CC, 1)

        ui.terminate()
        ui.join(timeout=2.0)

        # A single-path router cannot deliver while the router endpoint itself
        # is absent. This is deliberately tested only on the isolated endpoint.
        router_in.cancel_callback()
        router_in.close_port()
        while True:
            try:
                received.get_nowait()
            except queue.Empty:
                break
        try:
            sender.send_message([0xB0, TEST_CC, 41])
        except Exception as exc:
            print(f"Router unavailable: sender reported {type(exc).__name__}")
        time.sleep(0.2)
        if not received.empty():
            raise AssertionError("An output event appeared after router endpoint was closed")
        print("Router unavailable: no output observed (no automatic fail-open path)")

        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        time.sleep(2.0)
        idle_cpu_pct = 100.0 * (time.process_time() - cpu_start) / (time.perf_counter() - wall_start)
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        mean_latency = sum(latency_ms) / len(latency_ms) if latency_ms else None
        print(f"Virtual-route callback latency mean: {mean_latency:.3f} ms ({len(latency_ms)} samples)")
        print(f"Probe idle CPU over 2 s: {idle_cpu_pct:.3f}% of one logical CPU")
        print(f"Probe peak RSS: {peak_rss} bytes (macOS ru_maxrss)")
        print("Scope: synthetic virtual endpoints only; no AIRSTEP, SMC, P23, IAC, MainStage, or JamZone ports opened")

    finally:
        if ui is not None and ui.is_alive():
            try:
                os.kill(ui.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
            ui.terminate()
            ui.join(timeout=2.0)
        for port in (sender, observer, router_out, router_in):
            try:
                if port.is_port_open():
                    port.close_port()
            except Exception:
                pass


if __name__ == "__main__":
    main()
