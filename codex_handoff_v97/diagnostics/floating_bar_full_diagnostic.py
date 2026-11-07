#!/usr/bin/env python3
"""One-run, read-only Floating Bar diagnostic collector for macOS.

Collects only Floating Bar-related logs, relevant recent crash reports, process
state, and power/sleep evidence. Produces one ZIP on the Desktop. Does not send
MIDI, click/control apps, restart processes, alter settings, access personal
 documents, or use the network.
"""
import os, sys, re, json, time, shutil, hashlib, platform, subprocess, zipfile
from datetime import datetime
from pathlib import Path

HOME = Path.home()
DESKTOP = HOME / "Desktop"
LOG_DIR = HOME / "Library/Logs/MainStageJamZoneController"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
WORK = Path("/tmp") / f"floating_bar_full_diagnostic_{STAMP}"
ZIP_PATH = DESKTOP / f"floating_bar_full_diagnostic_{STAMP}.zip"
MAX_FILE = 25 * 1024 * 1024
MAX_TOTAL = 150 * 1024 * 1024
TOTAL = 0
MANIFEST = {}


def run(args, timeout=30):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors="replace", timeout=timeout)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        if isinstance(out, bytes): out = out.decode("utf-8", "replace")
        return "TIMEOUT", out
    except Exception as e:
        return "ERROR", f"{type(e).__name__}: {e}"


def write(rel, content):
    p = WORK / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", errors="replace")
    return p


def add_file(src, rel, note=""):
    global TOTAL
    src = Path(src)
    try:
        if not src.is_file():
            MANIFEST[rel] = {"status": "not_found", "note": note}
            return None
        size = src.stat().st_size
        if size > MAX_FILE:
            MANIFEST[rel] = {"status": "skipped_too_large", "size": size, "note": note}
            return None
        if TOTAL + size > MAX_TOTAL:
            MANIFEST[rel] = {"status": "skipped_total_limit", "size": size, "note": note}
            return None
        dest = WORK / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        TOTAL += size
        MANIFEST[rel] = {"status": "copied", "size": size,
                          "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(), "note": note}
        return dest
    except Exception as e:
        MANIFEST[rel] = {"status": "error", "note": f"{type(e).__name__}: {e}"}
        return None


def add_cmd(rel, args, timeout=30):
    rc, out = run(args, timeout)
    write(rel, f"Command: {' '.join(args)}\nReturn code: {rc}\n\n{out}\n")
    MANIFEST[rel] = {"status": "collected", "returncode": rc}
    return out


def analyze_diagnostic_log(src):
    if not src.is_file():
        write("analysis/diagnostic_log_summary.txt", f"Diagnostic log not found at expected path: {src}\n")
        return
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        write("analysis/diagnostic_log_summary.txt", f"Could not read diagnostic log: {type(e).__name__}: {e}\n")
        return
    lines = text.splitlines()
    patterns = {
        "ERROR / EXCEPTION": re.compile(r"error|exception|traceback|uncaught", re.I),
        "FREEZE / HEARTBEAT / WATCHDOG": re.compile(r"freeze|frozen|heartbeat|watchdog|stall|timeout", re.I),
        "SHUTDOWN / EXIT / RESTART": re.compile(r"shutdown|shutting|exit|restart|terminated|signal|crash", re.I),
        "NON-MAIN-THREAD TK CALLS": re.compile(r"TK_CALL_FROM_NON_MAIN_THREAD|non.main.thread|thread.*after|after.*thread", re.I),
        "MIDI THREAD / DEVICE": re.compile(r"coyotemidi|midi.*(start|stop|thread|error|disconnect)|receiver.*thread", re.I),
    }
    out = ["Floating Bar diagnostic log analysis", f"File: {src.name}", f"Total lines: {len(lines)}", "",
           "Counts are pattern matches, not proof of root cause.", "Personal paths and unrelated log content are intentionally omitted.", ""]
    for label, pat in patterns.items():
        hits = [(i + 1, line) for i, line in enumerate(lines) if pat.search(line)]
        out.append(f"{label}: {len(hits)} matching lines")
        # Keep only recent 25 matching lines, strip home path and common identity-bearing values.
        for n, line in hits[-25:]:
            line = line.replace(str(HOME), "~")
            line = re.sub(r"(?i)(username|user_name|account|email|serial number|serial):\s*[^,;]+", r"\1: [REDACTED]", line)
            out.append(f"  L{n}: {line[:500]}")
        out.append("")
    write("analysis/diagnostic_log_summary.txt", "\n".join(out))
    MANIFEST["analysis/diagnostic_log_summary.txt"] = {"status": "generated", "source_lines": len(lines)}


def main():
    if sys.platform != "darwin":
        print("ERROR: Run this file on the Mac (macOS only).")
        sys.exit(2)
    if not DESKTOP.is_dir():
        print("ERROR: Desktop folder not found; no files changed.")
        sys.exit(2)
    WORK.mkdir(parents=True, exist_ok=False)
    print("Floating Bar full diagnostic — read-only")
    print("No MIDI sent; no apps stopped/restarted; no settings changed; no network use.")
    print("Collecting relevant evidence into one ZIP on the Desktop…")

    env = {"created_local": datetime.now().astimezone().isoformat(),
           "macOS": platform.mac_ver(), "python": sys.version.split()[0],
           "machine": platform.machine(), "collector_version": "2.0",
           "privacy": "Selected Floating Bar diagnostics only; no personal documents or network upload."}
    write("system/environment.json", json.dumps(env, indent=2))

    # The log the current troubleshooting step needs.
    diag = LOG_DIR / "diagnostic_v1.log"
    copied = add_file(diag, "logs/diagnostic_v1.log", "Current Floating Bar diagnostic log.")
    analyze_diagnostic_log(diag)
    # Other explicitly relevant logs only.
    for name in ("crash_diagnostic_v1.log", "floating_bar_diag_v1_console.log"):
        for root in (LOG_DIR, DESKTOP, HOME / "Downloads"):
            p = root / name
            if p.is_file():
                add_file(p, f"logs/{name}", "Relevant Floating Bar diagnostic output.")
                break

    # Current process/resource snapshot; no command-line arguments collected.
    add_cmd("process/target_processes.txt", ["/usr/bin/pgrep", "-afil", "mainstage_jamzone_fade_controller|Floating Bar|floating_bar|JamZone|MainStage|CoyoteMIDI"])
    add_cmd("process/python_process_snapshot.txt", ["/bin/ps", "-axo", "pid,ppid,%cpu,%mem,rss,etime,stat,comm"])
    add_cmd("system/macOS_version.txt", ["/usr/bin/sw_vers"])
    add_cmd("system/disk_space.txt", ["/bin/df", "-h", str(DESKTOP)])
    add_cmd("system/power_settings.txt", ["/usr/bin/pmset", "-g", "custom"])
    add_cmd("system/uptime.txt", ["/usr/bin/uptime"])
    add_cmd("system/reboot_history.txt", ["/usr/bin/last", "reboot"], 20)
    add_cmd("system/shutdown_history.txt", ["/usr/bin/last", "shutdown"], 20)
    add_cmd("system/power_sleep_wake_log.txt", ["/usr/bin/log", "show", "--last", "48h", "--style", "compact", "--predicate",
        'eventMessage CONTAINS[c] "shutdown" OR eventMessage CONTAINS[c] "reboot" OR eventMessage CONTAINS[c] "sleep" OR eventMessage CONTAINS[c] "wake"'], 55)

    # Recent crash reports with filenames explicitly tied to the relevant apps/runtime.
    crash_dirs = [HOME / "Library/Logs/DiagnosticReports", Path("/Library/Logs/DiagnosticReports"), Path("/Library/Logs/CrashReporter")]
    cutoff = time.time() - 7 * 86400
    copied_crashes = 0
    for d in crash_dirs:
        if not d.is_dir(): continue
        try: candidates = sorted(d.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception: continue
        for f in candidates:
            try:
                if not f.is_file() or f.stat().st_mtime < cutoff: continue
                low = f.name.lower()
                if not any(k in low for k in ("python", "floating_bar", "floating-bar", "mainstage", "jamzone", "coyotemidi")): continue
                if f.suffix.lower() not in (".ips", ".crash", ".hang", ".spin", ".txt"): continue
                if add_file(f, f"crashes/{f.name}", "Recent crash report for a relevant app/runtime; inspect before sharing."):
                    copied_crashes += 1
                if copied_crashes >= 20: break
            except Exception: pass
        if copied_crashes >= 20: break
    MANIFEST["crashes/selection"] = {"status": "complete", "count": copied_crashes, "window_days": 7}

    write("MANIFEST.json", json.dumps(MANIFEST, indent=2, ensure_ascii=False))
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in WORK.rglob("*"):
            if p.is_file(): zf.write(p, p.relative_to(WORK))
    print(f"DONE: {ZIP_PATH}")
    print(f"ZIP size: {ZIP_PATH.stat().st_size:,} bytes")
    print(f"Collected records: {len(MANIFEST)}")
    print("Before sharing, note that system/crash logs can contain local usernames, paths, timestamps, and device/app identifiers.")

if __name__ == "__main__":
    main()
