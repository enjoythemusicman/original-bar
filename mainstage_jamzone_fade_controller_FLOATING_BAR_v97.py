#!/usr/bin/env python3
"""MainStage / JamZone MIDI controller with integrated floating status bar (v97: removes obsolete CH8 CC0 and CH1 CC57 fade-cancellation mappings).

Install: python3 -m pip install mido python-rtmidi pynput
Routing: AIRSTEP Bluetooth -> this script -> IAC Driver Bus 1 -> MainStage.

MainStage Master Volume mapping (outgoing IAC channel is forced to MIDI CH15):
  MIDI channel 15, CC20, Absolute
  CC value 127 = 0 dB
  CC value 0 = silence / -infinity

The fade sends absolute CC20 values from 127 down to 0 over 15 seconds,
after a 5-second delay. The CC-to-dB curve between calibration points is
approximated; only the measured points above are confirmed.
"""
import threading
import time
import subprocess
import json
import re
import faulthandler
import os
import sys
import traceback
import tkinter as tk
from tkinter import messagebox, filedialog, ttk
from datetime import datetime
from dataclasses import dataclass
import mido
from pynput import keyboard
try:
    import Quartz
    import CoreFoundation
except Exception:
    Quartz = None
    CoreFoundation = None


# ---------- Protection layer 1: crash / uncaught-exception diagnostics ----------
# This layer does not alter MIDI routing, timing, or fade logic. It records
# uncaught main-thread and worker-thread exceptions, plus fatal Python faults.
_CRASH_LOG_HANDLE = None
_CRASH_LOG_LOCK = threading.Lock()


def _write_crash_record(title, exc_type=None, exc_value=None, exc_tb=None):
    handle = _CRASH_LOG_HANDLE
    if handle is None:
        return
    try:
        with _CRASH_LOG_LOCK:
            handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {title}\n")
            if exc_type is not None:
                traceback.print_exception(exc_type, exc_value, exc_tb, file=handle)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        # Diagnostics must never be allowed to interrupt the live controller.
        pass


def _install_crash_diagnostics():
    global _CRASH_LOG_HANDLE
    try:
        log_dir = os.path.expanduser(
            '~/Library/Logs/MainStageJamZoneController'
        )
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, 'crash.log')
        _CRASH_LOG_HANDLE = open(log_path, 'a', encoding='utf-8', buffering=1)
        _CRASH_LOG_HANDLE.write(
            f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Controller process started; "
            f"Python {sys.version.split()[0]}\n"
        )
        if os.environ.pop('MAINSTAGE_JAMZONE_RESTART_PENDING', None) == '1':
            _CRASH_LOG_HANDLE.write(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                "FULL PROCESS RESTART COMPLETED: replacement process started successfully.\n"
            )
            print('[RESTART] Full process restart completed successfully.')
        _CRASH_LOG_HANDLE.flush()

        # Capture fatal interpreter/native faults when Python can still dump a traceback.
        faulthandler.enable(file=_CRASH_LOG_HANDLE, all_threads=True)

        previous_sys_hook = sys.excepthook

        def _main_exception_hook(exc_type, exc_value, exc_tb):
            _write_crash_record('UNCAUGHT MAIN-THREAD EXCEPTION', exc_type, exc_value, exc_tb)
            previous_sys_hook(exc_type, exc_value, exc_tb)

        sys.excepthook = _main_exception_hook

        previous_thread_hook = threading.excepthook

        def _thread_exception_hook(args):
            _write_crash_record(
                f'UNCAUGHT WORKER-THREAD EXCEPTION (thread={args.thread.name if args.thread else "unknown"})',
                args.exc_type, args.exc_value, args.exc_traceback,
            )
            previous_thread_hook(args)

        threading.excepthook = _thread_exception_hook
        print(f'[PROTECTION] Crash diagnostics enabled: {log_path}')
    except Exception as exc:
        # Failure to initialize diagnostics must not prevent the controller from running.
        print(f'[PROTECTION WARNING] Crash diagnostics unavailable: {type(exc).__name__}: {exc}')

AIRSTEP_NAME = "AIRSTEP Bluetooth"
IAC_NAME = "IAC Driver Bus 1"
IAC_STATE_INPUT_NAME = "IAC Driver Bus 2"

# Enable only for troubleshooting; packet-level logging can generate heavy console I/O.
VERBOSE_MIDI_LOGGING = False

# MIDI channels are zero-based in mido.
VOLUME_CHANNEL, VOLUME_CC = 14, 20     # IAC CH15 CC20, Absolute (full fade)
QUICK_FADE_CC = 21                      # IAC CH15 CC21, quick fades only
EFFECT_CC = 60  # IAC CH15 CC60, value 0 for quick-fade sidechain bypass
TT_CHANNEL, TT_CC = 15, 44             # CH16 CC44 starts/cancels TT1
PLAY_CHANNEL, PLAY_CC, PLAY_VALUE = 15, 22, 127  # CH16 CC22 triggers Quick Fade + P
GUITAR_CHANNEL, GUITAR_CC = 15, 42          # CH16 CC42, G2M during TT1's first 5 seconds
TB_CHANNEL, TB_CC = 15, 45                 # CH16 CC45, TalkBack during TT1's first 5 seconds
GUITAR_REPEAT_TIMEOUT_SECONDS = 2.0

PRE_FADE_DELAY_SECONDS = 5.0
FULL_FADE_SECONDS = 15.0
FULL_FADE_START_VALUE = 127            # calibrated 0 dB
FULL_FADE_END_VALUE = 0                # calibrated silence
SPECIAL_WINDOW_SECONDS = 5.0
SHORT_FADE_SECONDS = 1.0
SHORT_FADE_STEPS = 127
DOWN_TO_P_SECONDS = 1.5
P_TO_L_SECONDS = 0.5
LOCKOUT_SECONDS = 2.5
RESTORE_TARGET_VALUE = 127             # calibrated 0 dB

def format_midi_message(msg):
    """Format MIDI channels as 1-16, matching MainStage's display."""
    if hasattr(msg, "channel"):
        fields = [f"{key}={value}" for key, value in msg.dict().items() if key != "channel"]
        return f"{msg.type} channel={msg.channel + 1} " + " ".join(fields)
    return str(msg)

@dataclass
class FadeState:
    active: bool = False
    started_at: float = 0.0
    special_window_until: float = 0.0
    fade_started_at: float = 0.0
    current_value: int = FULL_FADE_START_VALUE
    short_action_active: bool = False
    down_sequence_active: bool = False
    quick_started_at: float = 0.0
    restore_master_after_30s: bool = False
    generation: int = 0


class FloatingBar:
    """Integrated status bar. UI reads controller state; it does not change MIDI routing."""
    BG = '#171b22'
    PANEL = '#292f3a'
    TEXT = '#d2d9e2'
    MUTED = '#929eae'
    BLUE_OFF = '#183d68'
    BLUE_ON = '#4d91d0'
    GREEN_ON = '#36a852'
    BLACK = '#080a0d'
    RED = '#e87575'
    YELLOW = '#e6bd59'
    ORANGE = '#bd762d'
    GUITAR_MUTE_COLOR = '#c58a32'  # muted guitar: subdued yellow-orange

    def __init__(self, root, controller):
        self.root = root
        self.controller = controller
        self.root.title('MainStage / JamZone Floating Bar — Integrated v97')
        self.root.configure(bg=self.BG)
        self.root.attributes('-topmost', True)
        self.root.resizable(False, False)
        self.width = self.root.winfo_screenwidth()
        self.screen_height = self.root.winfo_screenheight()
        self.root.geometry(f'{self.width}x50+0+0')
        self.controller.ui_heartbeat = time.monotonic()
        self._last_tile_colors = {}
        self.root.protocol('WM_DELETE_WINDOW', self._hide_window)
        self.visible = True
        self.font = ('Helvetica Neue', 16, 'bold')
        self.small_font = ('Helvetica Neue', 11, 'bold')
        bar = tk.Frame(root, bg=self.BG, padx=3, pady=2, height=50)
        bar.pack(fill='both', expand=True)
        bar.pack_propagate(False)

        def tile(parent, width, color, padx=2, height=44, side='left'):
            f = tk.Frame(parent, bg=color, padx=padx, pady=1, width=width, height=height, bd=0)
            f.pack(side=side, padx=1, fill='y')
            f.pack_propagate(False)
            return f

        def label(parent, text, bg, fg=None, font=None):
            w = tk.Label(parent, text=text, bg=bg, fg=fg or self.TEXT,
                         font=font or self.font, bd=0, padx=0, pady=0, anchor='center', justify='center')
            w.pack(expand=True, fill='both')
            return w

        self.reminder_colors = ['#26343b', '#482d55', '#174b46', '#60401f', '#344b70']
        self.message_settings_path = os.path.join(
            os.path.expanduser('~/Library/Application Support/MainStageJamZoneController'),
            'message_settings.json'
        )
        self._message_images = {}
        self._message_press_job = None
        self._message_click_job = None
        self._message_long_press_fired = False
        self.reminder_index = 0
        self._temporarily_skipped_messages = set()  # runtime only; reset automatically on process restart
        self._load_message_settings()
        self.reminder_tile = tile(bar, 230, self.reminder_colors[0], 2)
        self.reminder_label = label(self.reminder_tile, self.reminders[0], self.reminder_colors[0], '#f0f4f8', ('Helvetica Neue', 15, 'bold'))
        # Controller battery status occupies the upper-left edge of the reminder/message tile.
        # The reminder text is nudged down slightly so the two displays do not overlap.
        self.reminder_label.configure(font=('Helvetica Neue', 19, 'bold'), anchor='s', justify='center')
        self.reminder_label.pack_configure(pady=0)
        # Hover-only controls: skip this message for the current run, previous, next.
        self.message_hover_controls = tk.Frame(self.reminder_tile, bg=self.reminder_colors[0], bd=0)
        self.message_skip_button = tk.Button(self.message_hover_controls, text='×', command=self._skip_current_message,
            bg='#252a30', fg='#ffffff', activebackground='#555b64', activeforeground='#ffffff',
            relief='flat', bd=0, padx=3, pady=0, font=('Helvetica Neue', 12, 'bold'), cursor='hand2')
        self.message_next_button = tk.Button(self.message_hover_controls, text='→', command=lambda: self._cycle_reminder(-1),
            bg='#252a30', fg='#ffffff', activebackground='#555b64', activeforeground='#ffffff',
            relief='flat', bd=0, padx=3, pady=0, font=('Helvetica Neue', 11, 'bold'), cursor='hand2')
        self.message_prev_button = tk.Button(self.message_hover_controls, text='←', command=lambda: self._cycle_reminder(1),
            bg='#252a30', fg='#ffffff', activebackground='#555b64', activeforeground='#ffffff',
            relief='flat', bd=0, padx=3, pady=0, font=('Helvetica Neue', 11, 'bold'), cursor='hand2')
        # Right-to-left visual order from the far right: ×, right arrow, left arrow.
        self.message_skip_button.pack(side='right', padx=(0, 1))
        self.message_next_button.pack(side='right', padx=1)
        self.message_prev_button.pack(side='right', padx=1)
        self.message_hover_controls.place_forget()
        for _w in (self.reminder_tile, self.reminder_label):
            _w.bind('<Enter>', self._show_message_hover_controls, add='+')
            _w.bind('<Leave>', self._hide_message_hover_controls, add='+')
            _w.configure(cursor='hand2')
        for _w in (self.message_hover_controls, self.message_skip_button, self.message_next_button, self.message_prev_button):
            _w.bind('<Enter>', self._show_message_hover_controls, add='+')
            _w.bind('<Leave>', self._hide_message_hover_controls, add='+')

        # Separate labels let only an individual low-battery percentage blink red.
        # Font size 9 is approximately 12.5% larger than the previous size 8.
        self.controller_battery_labels = {}
        battery_label_specs = (('SMC', 3), ('P23', 88))
        for name, x in battery_label_specs:
            battery_label = tk.Label(
                self.reminder_tile, text=f'{name} --%',
                bg=self.reminder_colors[0], fg='#f0f4f8',
                font=('Helvetica Neue', 10, 'bold'), bd=0, padx=0, pady=0,
                anchor='w', width=10
            )
            battery_label.place(x=x, y=1, anchor='nw')
            self.controller_battery_labels[name] = battery_label
        self._controller_battery_levels = {'SMC': None, 'P23': None}
        self._controller_connected_state = {'SMC': None, 'P23': None}
        self._battery_blink_on = False
        self.battery_label = tk.Label(
            self.reminder_tile, text='🔋 --%', bg=self.reminder_colors[0],
            fg='#f0f4f8', font=('Helvetica Neue', 9, 'bold'), bd=0, padx=2,
            pady=0, anchor='e'
        )
        self.battery_label.place(relx=1.0, x=-3, y=1, anchor='ne')
        self._render_reminder()
        self._battery_check_running = False
        self._last_p23_json_poll_at = 0.0
        self._last_p23_json_battery_level = None
        self._battery_force_json_requested = False
        self._ui_refresh_pending = False
        self.root.after(1000, self._update_controller_batteries)  # Initial presence/battery snapshot
        self.root.after(1000, self._update_battery)
        self.root.after(0, self._controller_battery_flash_tick)

        # Removed the unused '--' placeholder tile; its width is added to the reminder tile.

        self.fx_tile = tile(bar, 48, self.BLUE_OFF, 1)
        self.fx_label = label(self.fx_tile, 'FX', self.BLUE_OFF, self.TEXT, self.font)
        self.fx_state = 'normal'  # 'muted' (red), 'normal' (light blue), 'no_ng' (orange)

        mode = tile(bar, 90, self.PANEL, 1)
        self.mode_label = label(mode, 'READY', self.PANEL, self.YELLOW, self.font)

        # Fade/TT/READY state is displayed in the same status tile; no separate black tile.
        self.fade_tile = mode
        self.fade_label = self.mode_label

        self.status_tiles = {}
        self.status_labels = {}
        for name in ['G2M', 'T.B.', 'MM', 'Bvox', 'Solos']:
            f = tile(bar, 48, self.BLUE_OFF, 1)
            status_label = label(f, name, self.BLUE_OFF, self.TEXT, self.font)
            if name == 'Bvox': status_label.configure(font=('Helvetica Neue', 19, 'bold'))
            if name == 'Solos': status_label.configure(font=('Helvetica Neue', 18, 'bold'))
            self.status_tiles[name] = f
            self.status_labels[name] = status_label

        # Compact one-line display of the latest MIDI command RECEIVED from AIRSTEP.
        self.midi_tile = tile(bar, 95, '#242c32', 3)
        self.midi_label = label(self.midi_tile, 'MIDI INPUT', '#242c32', '#f0f4f8', ('Helvetica Neue', 14, 'bold'))
        self.midi_label.place(relx=0.5, rely=0.5, anchor='center')
        self.disconnect_top = tk.Label(self.midi_tile, text='', bg='#242c32', fg='#171b22',
                                       font=('Helvetica Neue', 14, 'bold'), bd=0)
        self.disconnect_bottom = tk.Label(self.midi_tile, text='', bg='#242c32', fg='#171b22',
                                          font=('Helvetica Neue', 14, 'bold'), bd=0)
        self._disconnect_display_active = False
        self._disconnect_view_started_at = 0.0
        self._disconnect_show_error = True

        self.effect_tiles = {}
        self.effect_labels = {}
        for name in ['DLY', 'CHR', 'DIS', 'O.D.', 'FLG', 'WHA', 'IR']:
            f = tile(bar, 48, self.BLUE_OFF, 1)
            effect_label = label(f, name, self.BLUE_OFF, self.TEXT, self.font)
            if name == 'WHA': effect_label.configure(font=('Helvetica Neue', 19, 'bold'))
            self.effect_tiles[name] = f
            self.effect_labels[name] = effect_label

        # Clock and session timer share one tile on the right side of the bar.
        self.set_timer_seconds = int(self.message_settings.get('default_timer_seconds', 45 * 60))
        self.default_timer_seconds = self.set_timer_seconds
        self.session_remaining = self.set_timer_seconds
        self.session_deadline = None
        self.session_expired = False
        self._timer_flash_on = False
        self._timer_flash_active = False
        self.session_tile = tile(bar, 126, '#393326', 1, side='right')
        timer_text_frame = tk.Frame(self.session_tile, bg='#393326', bd=0)
        timer_text_frame.pack(side='left', fill='both', expand=True)
        self.clock_label = tk.Label(timer_text_frame, text=datetime.now().strftime('%H:%M'), bg='#393326', fg='#a9c4bd', font=('Helvetica Neue', 15, 'bold'), bd=0, padx=1, pady=0)
        self._last_clock_text = datetime.now().strftime('%H:%M')
        self.clock_label.pack(fill='x', expand=True)
        self.session_timer_label = tk.Label(timer_text_frame, text=self._format_duration(self.set_timer_seconds), bg='#393326', fg=self.YELLOW, font=('Helvetica Neue', 16, 'bold'), bd=0, padx=1, pady=0)
        self.session_timer_label.pack(fill='x', expand=True)
        timer_buttons = tk.Frame(self.session_tile, bg='#393326', bd=0, highlightthickness=0)
        timer_buttons.pack(side='right', fill='y')
        self.timer_toggle_button = tk.Button(timer_buttons, text='⏯️', command=self._session_click, font=('Helvetica Neue', 12), bd=0, padx=0, pady=0, bg='#393326', fg=self.TEXT, activebackground='#514936', relief='flat', highlightthickness=0, cursor='hand2')
        self.timer_toggle_button.pack(fill='x', expand=True)
        self.timer_reset_button = tk.Button(timer_buttons, text='🔄', command=self._reset_session_timer, font=('Helvetica Neue', 12), bd=0, padx=0, pady=0, bg='#393326', fg=self.TEXT, activebackground='#514936', relief='flat', highlightthickness=0, cursor='hand2')
        self.timer_reset_button.pack(fill='x', expand=True)
        self.bpm_tile = tile(bar, 168, '#242c32', 1, side='right')
        self.bpm_label = label(self.bpm_tile, 'BPM', '#242c32', self.YELLOW, ('Helvetica Neue', 23, 'bold'))
        self.bpm_label.configure(wraplength=164)
        self._timer_click_job = None
        self._timer_press_job = None
        self._timer_long_press_fired = False
        self._timer_double_click_seen = False
        # Reliable timer hit targets: all non-button areas support single-click toggle,
        # double-click edit, and long-press edit. Dedicated icon buttons remain independent.
        for timer_widget in (self.session_tile, timer_text_frame, self.clock_label, self.session_timer_label):
            timer_widget.bind('<ButtonPress-1>', self._timer_press, add='+')
            timer_widget.bind('<ButtonRelease-1>', self._timer_release, add='+')
            timer_widget.bind('<Double-Button-1>', self._timer_double_press, add='+')
            timer_widget.configure(cursor='hand2')
        for timer_button in (self.timer_toggle_button, self.timer_reset_button):
            timer_button.configure(cursor='hand2', padx=5, pady=3)

        self.root.bind('<Escape>', lambda _e: self._hide_window())
        self.root.bind('m', self._cycle_reminder)
        # Physical § triggers an immediate full-process restart without confirmation.
        # The keyboard listener only raises a request flag; Tk performs the restart.
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(
            label='Restart controller…  § (physical key)',
            command=self._confirm_restart
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(label='Show bar', command=self._show_window)
        self._bind_context_menu_recursive(self.root)
        self._bind_message_long_press(self.reminder_tile)
        self._bind_message_long_press(self.reminder_label)
        self._fault_started_at = None
        self._fault_message = ''
        self._fault_was_visible = False
        self._missing_port_checks = 0
        self.root.after(1000, self.tick)
        self.root.after(30000, self._update_clock)
        self._reminder_cycle_job = self.root.after(self._current_reminder_duration_ms(), self._auto_cycle_reminder)
        self._session_flash_job = None
        self._mode_flash_job = None
        self._battery_flash_job = None
        self.root.after(0, self._session_flash_tick)
        self.root.after(0, self._mode_flash_tick)

    def _default_message_settings(self):
        defaults = [
            {'text': '🎤 תציג עצמך 🎤', 'duration': 10, 'image': ''},
            {'text': '🍷 לחיים 🥂', 'duration': 10, 'image': ''},
            {'text': '📷 אינסטה 📷', 'duration': 10, 'image': ''},
            {'text': '🍸 משקה הבית 🍹', 'duration': 10, 'image': ''},
            {'text': '🇵🇹 🇮🇹 מאיפה אתם? 🇪🇸 🇫🇷', 'duration': 10, 'image': ''},
        ]
        for row in defaults:
            row['link_all'] = True
        return {'rows': defaults, 'global_duration': 10}

    def _load_message_settings(self):
        self.message_settings = self._default_message_settings()
        try:
            with open(self.message_settings_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            rows = data.get('rows')
            if isinstance(rows, list) and rows:
                clean = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    try:
                        duration = max(1, min(3600, int(row.get('duration', 10))))
                    except (TypeError, ValueError):
                        duration = 10
                    clean.append({
                        'text': str(row.get('text', '')),
                        'duration': duration,
                        'image': str(row.get('image', '') or ''),
                        'link_all': bool(row.get('link_all', True)),
                        'show': bool(row.get('show', bool(str(row.get('text', '')).strip()))),
                    })
                if clean:
                    self.message_settings = {
                        'rows': clean,
                        'global_duration': max(1, min(3600, int(data.get('global_duration', 10)))),
                        'remember_timer_default': bool(data.get('remember_timer_default', True)),
                        'default_timer_seconds': max(60, min(599940, int(data.get('default_timer_seconds', 45 * 60)))),
                        'presets': data.get('presets', {}) if isinstance(data.get('presets', {}), dict) else {},
                        'active_preset': str(data.get('active_preset', ''))
                    }
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f'[MESSAGES] Could not load saved settings; using defaults: {exc}')
        self._sync_reminder_compatibility()

    def _save_message_settings(self):
        try:
            os.makedirs(os.path.dirname(self.message_settings_path), exist_ok=True)
            temp_path = self.message_settings_path + '.tmp'
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(self.message_settings, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.message_settings_path)
            print(f'[MESSAGES] Settings saved to {self.message_settings_path}')
        except Exception as exc:
            messagebox.showerror('Message settings', f'Could not save settings:\n{exc}', parent=self.root)

    def _sync_reminder_compatibility(self):
        self.reminders = [row.get('text', '') for row in self.message_settings['rows']]
        if not any(row.get('show', bool(row.get('text', '').strip())) for row in self.message_settings['rows']):
            for row in self.message_settings['rows']:
                row['show'] = bool(row.get('text', '').strip())
        if not self.reminders:
            self.message_settings = self._default_message_settings()
            self.reminders = [row['text'] for row in self.message_settings['rows']]
        palette = ['#26343b', '#482d55', '#174b46', '#60401f', '#344b70']
        self.reminder_colors = [palette[i % len(palette)] for i in range(len(self.reminders))]
        self.reminder_index %= len(self.reminders)

    def _current_reminder_duration_ms(self):
        rows = self.message_settings.get('rows', [])
        if not rows:
            return 10000
        try:
            seconds = max(1, min(3600, int(rows[self.reminder_index].get('duration', 10))))
        except (ValueError, TypeError, IndexError):
            seconds = 10
        return seconds * 1000

    @staticmethod
    def _message_direction(text):
        # Tkinter does not expose reliable automatic bidi layout on all macOS Tk builds.
        # Choose RTL when Hebrew/Arabic characters are present; otherwise use LTR.
        return 'rtl' if re.search(r'[\u0590-\u08ff]', text or '') else 'ltr'

    def _visible_message_indices(self):
        return [i for i, row in enumerate(self.message_settings.get('rows', []))
                if i not in getattr(self, '_temporarily_skipped_messages', set()) and row.get('show', bool(row.get('text', '').strip()))]

    def _render_reminder(self):
        if not hasattr(self, 'reminder_label'):
            return
        visible = self._visible_message_indices()
        if not visible:
            self.reminder_label.configure(text='', image='', compound='none')
            return
        if self.reminder_index not in visible:
            self.reminder_index = visible[0]
        row = self.message_settings['rows'][self.reminder_index]
        text = row.get('text', '')
        color = self.reminder_colors[self.reminder_index % len(self.reminder_colors)]
        direction = self._message_direction(text)
        self.reminder_label.configure(text=text, image='', compound='none', bg=color, fg='#f0f4f8',
            anchor=('s' if direction == 'rtl' else 'se'), justify=('right' if direction == 'rtl' else 'left'))
        self.reminder_tile.configure(bg=color)
        for battery_label in self.controller_battery_labels.values():
            battery_label.configure(bg=color)
        self.battery_label.configure(bg=color)

    def _show_message_hover_controls(self, _event=None):
        try:
            self.message_hover_controls.configure(bg=self.reminder_colors[self.reminder_index % len(self.reminder_colors)])
            self.message_hover_controls.place(relx=1.0, x=-2, y=18, anchor='ne')
            self.message_hover_controls.lift()
        except tk.TclError:
            pass

    def _hide_message_hover_controls(self, _event=None):
        try:
            # Moving from the tile to its controls must not hide them.
            x, y = self.root.winfo_pointerxy()
            widget = self.root.winfo_containing(x, y)
            if widget is not None and (widget == self.message_hover_controls or str(widget).startswith(str(self.message_hover_controls))):
                return
            self.message_hover_controls.place_forget()
        except tk.TclError:
            pass

    def _skip_current_message(self):
        self._temporarily_skipped_messages.add(self.reminder_index)
        self.message_hover_controls.place_forget()
        visible = self._visible_message_indices()
        if visible:
            self.reminder_index = visible[0] if self.reminder_index not in visible else self.reminder_index
            self._cycle_reminder(1)
        else:
            self.reminder_label.configure(text='')
        print(f'[MESSAGES] Temporarily skipped row {self.reminder_index + 1}; reset on next process launch.')

    def _bind_message_long_press(self, widget):
        widget.bind('<ButtonPress-1>', self._message_press, add='+')
        widget.bind('<ButtonRelease-1>', self._message_release, add='+')
        widget.bind('<Double-Button-1>', self._open_message_editor_event, add='+')
        for child in widget.winfo_children():
            # Hover controls are independent click targets and must not trigger navigation.
            if child in (getattr(self, 'message_hover_controls', None),):
                continue
            self._bind_message_long_press(child)

    def _message_press(self, _event=None):
        self._message_long_press_fired = False
        if self._message_press_job is not None:
            try: self.root.after_cancel(self._message_press_job)
            except tk.TclError: pass
        self._message_press_job = self.root.after(700, self._open_message_editor)

    def _message_release(self, _event=None):
        if self._message_press_job is not None:
            try: self.root.after_cancel(self._message_press_job)
            except tk.TclError: pass
            self._message_press_job = None
        if not self._message_long_press_fired:
            direction = -1 if (_event is not None and _event.x >= max(1, self.reminder_tile.winfo_width() // 2)) else 1
            if self._message_click_job is not None:
                try: self.root.after_cancel(self._message_click_job)
                except tk.TclError: pass
            self._message_click_job = self.root.after(260, lambda d=direction: self._cycle_reminder(d))

    def _open_message_editor_event(self, _event=None):
        if self._message_click_job is not None:
            try: self.root.after_cancel(self._message_click_job)
            except tk.TclError: pass
            self._message_click_job = None
        if self._message_press_job is not None:
            try: self.root.after_cancel(self._message_press_job)
            except tk.TclError: pass
            self._message_press_job = None
        self._open_message_editor()
        return 'break'

    def _open_message_editor(self):
        self._message_press_job = None
        self._message_long_press_fired = True
        existing = getattr(self, '_message_editor', None)
        if existing is not None:
            try:
                if existing.winfo_exists(): existing.deiconify(); existing.lift(); existing.focus_force(); return
            except tk.TclError: pass
        win = tk.Toplevel(self.root)
        self._message_editor = win
        win.title('Message box settings')
        win.geometry('1120x650+0+52')
        win.minsize(900, 420)
        win.configure(bg='#303030')
        win.attributes('-topmost', True)
        # Keep the editor below the 50px bar and make it a fully active window.
        win.transient('')
        win.update_idletasks()
        try:
            x = max(0, self.root.winfo_rootx())
            y = self.root.winfo_rooty() + self.root.winfo_height() + 2
            win.geometry(f'+{x}+{y}')
        except tk.TclError: pass
        outer = tk.Frame(win, padx=10, pady=8, bg='#303030'); outer.pack(fill='both', expand=True)
        top = tk.Frame(outer, bg='#303030'); top.pack(fill='x', pady=(0, 8))
        tk.Label(top, text='Rows:', bg='#303030', fg='white').pack(side='left')
        count_var = tk.StringVar(value=str(len(self.message_settings['rows'])))
        tk.Entry(top, textvariable=count_var, width=4).pack(side='left', padx=(4, 12))
        tk.Button(top, text='Set', command=lambda: apply_row_count()).pack(side='left', padx=(0, 12))
        tk.Label(top, text='Global seconds:', bg='#303030', fg='white').pack(side='left')
        global_var = tk.StringVar(value=str(self.message_settings.get('global_duration', 10)))
        global_entry = tk.Entry(top, textvariable=global_var, width=5); global_entry.pack(side='left', padx=4)
        tk.Button(top, text='Apply All', command=lambda: apply_global_to_linked()).pack(side='left', padx=4)
        preset_var = tk.StringVar(value=self.message_settings.get('active_preset', ''))
        tk.Label(top, text='Preset:', bg='#303030', fg='white').pack(side='left', padx=(12, 2))
        preset_entry = tk.Entry(top, textvariable=preset_var, width=14); preset_entry.pack(side='left', padx=2)
        def choose_preset(mode):
            names = sorted(self.message_settings.get('presets', {}).keys(), key=str.casefold)
            dlg = tk.Toplevel(win); dlg.title('Choose message set'); dlg.transient(win); dlg.grab_set()
            dlg.attributes('-topmost', True); dlg.geometry('360x360')
            tk.Label(dlg, text=('Load a set' if mode == 'load' else 'Choose a set to save/overwrite'),
                     font=('Helvetica Neue', 13, 'bold')).pack(pady=10)
            lb = tk.Listbox(dlg, height=12, exportselection=False); lb.pack(fill='both', expand=True, padx=12, pady=5)
            for name in names: lb.insert('end', name)
            if preset_var.get().strip() in names: lb.selection_set(names.index(preset_var.get().strip()))
            def choose(_event=None):
                sel = lb.curselection()
                if not sel: return
                name = names[sel[0]]; preset_var.set(name)
                dlg.destroy()
                if mode == 'load': load_preset(name)
                else: save_named_preset(name)
            lb.bind('<Double-Button-1>', choose)
            buttons = tk.Frame(dlg); buttons.pack(fill='x', padx=12, pady=8)
            tk.Button(buttons, text='Select', command=choose).pack(side='left')
            def use_typed_name():
                name = preset_var.get().strip()
                if not name:
                    messagebox.showerror('Message settings', 'Enter a new set name in the Preset field first.', parent=dlg)
                    return
                dlg.destroy()
                if mode == 'save': save_named_preset(name)
                else: load_preset(name)
            tk.Button(buttons, text=('Save typed name' if mode == 'save' else 'Load typed name'), command=use_typed_name).pack(side='left', padx=6)
            tk.Button(buttons, text='Cancel', command=dlg.destroy).pack(side='right')
            if not names: tk.Label(dlg, text='No saved sets yet. Enter a new name in Preset.').pack(pady=6)
            dlg.wait_window()
        rows_host = tk.Frame(outer, bg='#303030'); rows_host.pack(fill='both', expand=True)
        canvas = tk.Canvas(rows_host, highlightthickness=0, bg='#303030')
        scrollbar = ttk.Scrollbar(rows_host, orient='vertical', command=canvas.yview)
        inner = tk.Frame(canvas, bg='#303030')
        inner.bind('<Configure>', lambda _e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas_window = canvas.create_window((0,0), window=inner, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side='left', fill='both', expand=True); scrollbar.pack(side='right', fill='y')
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(canvas_window, width=e.width))
        row_controls=[]; source_rows=[dict(r) for r in self.message_settings['rows']]
        def apply_global_to_linked(*_args):
            try: seconds=max(1,min(3600,int(global_var.get())))
            except ValueError: return
            for item in row_controls:
                if item['link'].get(): item['duration'].set(str(seconds))
        global_entry.bind('<Return>', apply_global_to_linked)
        def rebuild_rows(rows):
            for child in inner.winfo_children(): child.destroy()
            row_controls.clear()
            for text,col in [('#',0),('Message text (max 32 chars)',1),('Seconds',2),('Global',3),('Show',4)]:
                tk.Label(inner,text=text,anchor='w',bg='#303030',fg='white').grid(row=0,column=col,sticky='w',padx=3)
            inner.grid_columnconfigure(1,weight=1)
            for idx,row in enumerate(rows,start=1):
                tk.Label(inner,text=str(idx),width=3,anchor='w',bg='#303030',fg='white').grid(row=idx,column=0,sticky='nw',pady=4)
                tv=tk.StringVar(value=str(row.get('text',''))[:32]); ent=tk.Entry(inner,textvariable=tv,width=32); ent.grid(row=idx,column=1,sticky='ew',padx=3,pady=4)
                ent.configure(validate='key',validatecommand=(win.register(lambda proposed: len(proposed)<=32),'%P'))
                dv=tk.StringVar(value=str(row.get('duration',10))); tk.Entry(inner,textvariable=dv,width=6).grid(row=idx,column=2,padx=3,pady=4)
                link=tk.BooleanVar(value=bool(row.get('link_all',True))); tk.Checkbutton(inner,variable=link).grid(row=idx,column=3,padx=3)
                show=tk.BooleanVar(value=bool(row.get('show',bool(str(row.get('text','')).strip())))); tk.Checkbutton(inner,variable=show).grid(row=idx,column=4,padx=3)
                row_controls.append({'text':tv,'duration':dv,'link':link,'show':show,'original_text':str(row.get('text',''))})
        def current_rows():
            result=[]
            for item in row_controls:
                text=item['text'].get()[:32]
                was=item['original_text']
                # New non-empty text rows are automatically included in Show.
                show=bool(item['show'].get()) or (bool(text.strip()) and not was.strip())
                result.append({'text':text,'duration':max(1,min(3600,int(item['duration'].get()))),
                    'link_all':bool(item['link'].get()),'show':show,'image':''})
            return result
        def apply_row_count():
            try:
                count=int(count_var.get())
                if not 1<=count<=50: raise ValueError
            except ValueError:
                messagebox.showerror('Message settings','Enter a row count from 1 to 50.',parent=win); return
            rows=[]
            for i in range(count):
                if i<len(row_controls):
                    item=row_controls[i]; rows.append({'text':item['text'].get(),'duration':item['duration'].get(),'link_all':item['link'].get(),'show':item['show'].get()})
                elif i<len(source_rows): rows.append(dict(source_rows[i]))
                else: rows.append({'text':'','duration':10,'link_all':True,'show':False})
            source_rows[:]=rows; rebuild_rows(source_rows)
        def load_preset(name=None):
            name=(name or preset_var.get()).strip(); preset=self.message_settings.get('presets',{}).get(name)
            if not isinstance(preset,dict):
                messagebox.showerror('Message settings','Preset not found.',parent=win); return
            rows=preset.get('rows',[])
            if not isinstance(rows,list) or not rows:
                messagebox.showerror('Message settings','The selected preset has no rows.',parent=win); return
            source_rows[:]=[dict(r) for r in rows[:50]]; count_var.set(str(len(source_rows)))
            global_var.set(str(preset.get('global_duration',10))); rebuild_rows(source_rows)
        def save_named_preset(name=None):
            name=(name or preset_var.get()).strip()
            if not name:
                messagebox.showerror('Message settings','Enter a set name first.',parent=win); return
            try: rows=current_rows(); global_seconds=max(1,min(3600,int(global_var.get())))
            except ValueError:
                messagebox.showerror('Message settings','Check row seconds and global seconds.',parent=win); return
            presets=dict(self.message_settings.get('presets',{})); existed=name in presets
            new_texts={r['text'].strip() for r in rows if r['text'].strip()}
            previous_texts={r.get('text','').strip() for r in presets.get(name,{}).get('rows',[]) if r.get('text','').strip()} if existed else set()
            additions=new_texts-previous_texts
            presets[name]={'rows':[dict(r) for r in rows],'global_duration':global_seconds}
            self.message_settings['presets']=presets; self.message_settings['active_preset']=name; preset_var.set(name)
            if additions:
                others=[n for n in sorted(presets,key=str.casefold) if n != name]
                if others:
                    dlg=tk.Toplevel(win); dlg.title('Add new rows to other sets?'); dlg.transient(win); dlg.grab_set(); dlg.attributes('-topmost',True)
                    tk.Label(dlg,text='האם להוסיף את השורות החדשות לסטים נוספים?',font=('Helvetica Neue',13,'bold'),wraplength=420).pack(padx=14,pady=12)
                    choices=tk.Frame(dlg); choices.pack(fill='both',expand=True,padx=12)
                    vars_by_name={}
                    for n in others:
                        var=tk.BooleanVar(value=False); vars_by_name[n]=var; tk.Checkbutton(choices,text=n,variable=var,anchor='w').pack(fill='x')
                    all_state={'value':False}
                    def toggle_all():
                        all_state['value']=not all_state['value']
                        for var in vars_by_name.values(): var.set(all_state['value'])
                        all_button.configure(text='None' if all_state['value'] else 'All')
                    all_button=tk.Button(dlg,text='All',command=toggle_all); all_button.pack(side='left',padx=12,pady=10)
                    def apply_additions():
                        for n,var in vars_by_name.items():
                            if var.get():
                                other=dict(presets[n]); old=list(other.get('rows',[])); existing_text={r.get('text','').strip() for r in old}
                                for r in rows:
                                    if r['text'].strip() and r['text'].strip() in additions and r['text'].strip() not in existing_text:
                                        nr=dict(r); nr['show']=True; old.append(nr); existing_text.add(nr['text'].strip())
                                other['rows']=old[:50]; presets[n]=other
                        self.message_settings['presets']=presets; dlg.destroy()
                    tk.Button(dlg,text='Apply',command=apply_additions).pack(side='right',padx=12,pady=10)
                    dlg.wait_window()
            self.message_settings['presets']=presets; self.message_settings['active_preset']=name
            self._save_message_settings()
            messagebox.showinfo('Message settings',f'Set "{name}" saved. Saved sets are limited by available disk space; no fixed count is imposed by this script.',parent=win)
        tk.Button(top,text='Load',command=lambda: choose_preset('load')).pack(side='left',padx=3)
        tk.Button(top,text='Save Set',command=lambda: choose_preset('save')).pack(side='left',padx=3)
        rebuild_rows(source_rows)
        def save_and_close():
            try:
                count=int(count_var.get())
                if not 1<=count<=50 or count!=len(row_controls): raise ValueError
                global_seconds=max(1,min(3600,int(global_var.get())))
                rows=current_rows()
            except ValueError:
                messagebox.showerror('Message settings','Check row count, row seconds, and global seconds.',parent=win); return
            payload=dict(self.message_settings); payload.update({'rows':rows,'global_duration':global_seconds})
            self.message_settings=payload
            self._sync_reminder_compatibility(); self.reminder_index=0; self._render_reminder()
            self._save_message_settings()
            try: self.root.after_cancel(self._reminder_cycle_job)
            except (AttributeError,tk.TclError): pass
            self._reminder_cycle_job=self.root.after(self._current_reminder_duration_ms(),self._auto_cycle_reminder)
            win.destroy(); self._message_editor=None
        bottom=tk.Frame(outer,bg='#303030'); bottom.pack(fill='x',pady=(6,0))
        tk.Label(bottom,text='Text limit: 32 characters. Bottom Save stores current display settings for next launch.',bg='#303030',fg='white').pack(side='left')
        tk.Button(bottom,text='Save',command=save_and_close).pack(side='right',padx=4)
        tk.Button(bottom,text='Cancel',command=lambda: (win.destroy(),setattr(self,'_message_editor',None))).pack(side='right')
        win.protocol('WM_DELETE_WINDOW',lambda: (win.destroy(),setattr(self,'_message_editor',None)))
        win.lift(); win.focus_force()

    def _bind_context_menu_recursive(self, widget):
        widget.bind('<Button-2>', self._show_context_menu, add='+')
        widget.bind('<Button-3>', self._show_context_menu, add='+')
        for child in widget.winfo_children():
            self._bind_context_menu_recursive(child)

    @staticmethod
    def _paint_tree(widget, color):
        try:
            widget.configure(bg=color)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            FloatingBar._paint_tree(child, color)

    def _hide_window(self):
        editor = getattr(self, '_message_editor', None)
        try:
            if editor is not None and editor.winfo_exists(): editor.withdraw()
        except tk.TclError:
            self._message_editor = None
        self.root.withdraw()
        self.visible = False

    def _show_window(self):
        self.root.deiconify()
        self.root.attributes('-topmost', True)
        self.visible = True
        editor = getattr(self, '_message_editor', None)
        try:
            if editor is not None and editor.winfo_exists():
                editor.deiconify(); editor.lift()
        except tk.TclError:
            self._message_editor = None

    def _toggle_bar_visibility(self):
        if self.visible:
            self._hide_window()
        else:
            self._show_window()
        print(f"[BAR VISIBILITY] {'shown' if self.visible else 'hidden'} by SMC CH1 Note 93")

    def _show_context_menu(self, event):
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def _confirm_restart(self, source='menu'):
        # The physical § shortcut is an immediate full-process reset; no dialog.
        # Keep confirmation for the context-menu action to avoid accidental clicks.
        if source != '§' and not messagebox.askyesno(
                'Restart controller',
                'This will fully restart the controller process. Continue?'):
            print(f'[RESTART] Cancelled by user (source={source}).')
            return
        self.controller.restart_requested = True
        self.controller.restart_source = source
        _write_crash_record(f'FULL PROCESS RESTART REQUESTED (source={source})')
        print(f'[RESTART] Confirmed by user (source={source}); closing MIDI resources.')
        self.root.quit()

    def _show_reminder(self):
        self._render_reminder()

    @staticmethod
    def _extract_controller_battery_levels(data):
        """Extract explicit battery values from macOS Bluetooth JSON, retaining device context."""
        import re
        targets = {
            'AIR': ('airstep', 'air step'),
            'SMC': ('smc-mixer', 'smc mixer'),
            'P23': ('p23', 'pt23', 'pt 23', 'p-23', 'airpods p23'),
        }
        found = {key: None for key in targets}
        battery_key_re = re.compile(r'(battery|charge|power[_ ]?level|powerlevel|batterypercent|batterypercentage)', re.I)

        def parse_number(value):
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                number = int(value)
            elif isinstance(value, str):
                text = value.strip()
                match = re.fullmatch(r'(\d{1,3})\s*%?', text)
                number = int(match.group(1)) if match else None
            else:
                number = None
            return number if number is not None and 0 <= number <= 100 else None

        def labels_in(text):
            lowered = str(text).lower().replace('_', ' ').replace('-', ' ')
            return tuple(label for label, aliases in targets.items()
                         if any(alias.replace('_', ' ').replace('-', ' ') in lowered for alias in aliases))

        def battery_values(value):
            values = []
            number = parse_number(value)
            if number is not None:
                values.append(number)
            elif isinstance(value, dict):
                for child in value.values():
                    values.extend(battery_values(child))
            elif isinstance(value, list):
                for child in value:
                    values.extend(battery_values(child))
            return values

        def visit(node, inherited_targets=(), battery_context=False):
            if isinstance(node, dict):
                identity_text = ' '.join([str(key) for key in node.keys()] +
                                         [str(value) for value in node.values()
                                          if isinstance(value, (str, int, float))]).lower()
                local_targets = labels_in(identity_text) or inherited_targets
                for key, value in node.items():
                    is_battery_field = battery_context or bool(battery_key_re.search(str(key)))
                    if is_battery_field:
                        candidates = battery_values(value)
                        if candidates:
                            for label in local_targets:
                                if found[label] is None:
                                    found[label] = candidates[0]
                    # Recurse normally to support nested device trees and keys as identities.
                    visit(value, labels_in(key) or local_targets, bool(battery_key_re.search(str(key))))
            elif isinstance(node, list):
                for value in node:
                    visit(value, inherited_targets, battery_context)
        visit(data)
        return found

    @staticmethod
    def _extract_controller_battery_levels_text(report):
        """Parse macOS Bluetooth device blocks without losing the device at nested headings."""
        import re
        found = {'SMC': None, 'P23': None}
        connected = {'SMC': False, 'P23': False}
        seen = {'SMC': False, 'P23': False}
        aliases = {
            'SMC': ('smc-mixer', 'smc mixer'),
            'P23': ('pt23', 'pt 23', 'p23', 'p-23', 'airpods p23'),
        }
        current_targets = ()
        target_indent = None
        for line in report.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            indent = len(line) - len(line.lstrip())
            if stripped.endswith(':') and not stripped.lower().startswith(('bluetooth:', 'devices:', 'system profiler:')):
                heading = stripped[:-1].lower()
                matched = tuple(label for label, names in aliases.items() if any(name in heading for name in names))
                if matched:
                    current_targets = matched
                    target_indent = indent
                    for label in matched:
                        connected[label] = True
                        seen[label] = True
                elif target_indent is not None and indent <= target_indent:
                    current_targets = ()
                    target_indent = None
            if not current_targets:
                continue
            if re.search(r'connected\s*:\s*(yes|true)', stripped, re.I):
                for label in current_targets:
                    connected[label] = True
            elif re.search(r'connected\s*:\s*(no|false)', stripped, re.I):
                for label in current_targets:
                    connected[label] = False
            match = re.search(r'(?:battery(?:\s+level|\s+percentage)?|charge(?:\s+level)?)\s*[:=]?\s*(\d{1,3})\s*%?', stripped, re.I)
            if not match:
                match = re.search(r'(\d{1,3})\s*%.*battery|battery.*?(\d{1,3})\s*%', stripped, re.I)
            if match:
                value = next((int(g) for g in match.groups() if g is not None), None)
                if value is not None and 0 <= value <= 100:
                    for label in current_targets:
                        found[label] = value
        return found, connected, seen

    def _update_controller_batteries(self):
        """Run one Bluetooth/battery snapshot at startup and on the 15-minute schedule."""
        if self._battery_check_running:
            return
        self._battery_check_running = True

        def worker():
            levels = dict(getattr(self, '_controller_battery_levels', {'SMC': None, 'P23': None}))
            connected = dict(getattr(self, '_controller_connected_state', {'SMC': False, 'P23': False}))
            scan_succeeded = False
            try:
                result = subprocess.run(
                    ['system_profiler', 'SPBluetoothDataType'],
                    capture_output=True, text=True, timeout=30, check=False
                )
                if result.returncode == 0 and result.stdout.strip():
                    text_levels, text_connected, text_seen = self._extract_controller_battery_levels_text(result.stdout)
                    levels = text_levels
                    connected = text_connected
                    scan_succeeded = True
                else:
                    print(f'[BATTERY] Bluetooth scan failed (exit={result.returncode}); keeping last known state.')
                    text_seen = {'SMC': False, 'P23': False}

                # P23 battery values were previously available through macOS JSON. Use that
                # fallback once per scheduled scan when the text report lacks its percentage.
                if scan_succeeded and levels.get('P23') is None:
                    json_result = subprocess.run(
                        ['system_profiler', '-json', 'SPBluetoothDataType'],
                        capture_output=True, text=True, timeout=30, check=False
                    )
                    if json_result.returncode == 0 and json_result.stdout.strip():
                        json_levels = self._extract_controller_battery_levels(json.loads(json_result.stdout))
                        p23_level = json_levels.get('P23')
                        # Do not let a stale JSON battery value override an explicit Connected: No.
                        if p23_level is not None and not (text_seen.get('P23') and not text_connected.get('P23', False)):
                            levels['P23'] = p23_level
                            connected['P23'] = True
                        elif connected.get('P23') and self._last_p23_json_battery_level is not None:
                            levels['P23'] = self._last_p23_json_battery_level
                    elif VERBOSE_MIDI_LOGGING:
                        print('[BATTERY] P23 JSON fallback unavailable.')

                # If the scan succeeded but P23 was absent from both formats, treat it as disconnected.
                # If the scan itself failed, retain the last known state rather than showing a false disconnect.
                if scan_succeeded:
                    if levels.get('P23') is not None:
                        self._last_p23_json_battery_level = levels['P23']
                    elif not connected.get('P23', False):
                        levels['P23'] = None
                    # SMC battery is exposed by macOS through pmset -g accps.
                    smc_result = subprocess.run(
                        ['pmset', '-g', 'accps'], capture_output=True, text=True, timeout=5, check=False
                    )
                    match = re.search(r'SMC-Mixer[^\n]*?(\d{1,3})\s*%', smc_result.stdout, re.I)
                    if match:
                        value = int(match.group(1))
                        if 0 <= value <= 100:
                            levels['SMC'] = value
                            connected['SMC'] = True
                    for device_name in ('SMC', 'P23'):
                        if levels.get(device_name) is not None:
                            connected[device_name] = True
                else:
                    # Keep last known battery percentages and presence after an OS scan failure.
                    levels = dict(getattr(self, '_controller_battery_levels', levels))
                    connected = dict(getattr(self, '_controller_connected_state', connected))
                if VERBOSE_MIDI_LOGGING:
                    print(f"[BATTERY] Present={connected}; Read results: SMC={levels.get('SMC')}%, P23={levels.get('P23')}%.")
            except Exception as exc:
                print(f'[BATTERY] Could not read Bluetooth battery levels: {type(exc).__name__}: {exc}')
                levels = dict(getattr(self, '_controller_battery_levels', levels))
                connected = dict(getattr(self, '_controller_connected_state', connected))
            finally:
                self._battery_check_running = False
            previous = dict(getattr(self, '_controller_connected_state', {}))
            newly_connected = [name for name in ('SMC', 'P23')
                               if connected.get(name) is True and previous.get(name) is not True]
            newly_disconnected = [name for name in ('SMC', 'P23')
                                  if connected.get(name) is False and previous.get(name) is True]
            if newly_connected:
                print('[BATTERY] Connected: ' + ', '.join(newly_connected))
            if newly_disconnected:
                print('[BATTERY] Disconnected: ' + ', '.join(newly_disconnected))
            try:
                self.root.after(0, lambda l=dict(levels), c=dict(connected): self._apply_controller_batteries(l, c))
            except (AttributeError, tk.TclError):
                pass
        threading.Thread(target=worker, name='ControllerBatteryPoll', daemon=True).start()

    def request_ui_refresh(self):
        """Coalesce MIDI bursts to at most 10 full UI refreshes per second."""
        if self._ui_refresh_pending:
            return
        self._ui_refresh_pending = True
        try:
            # A 100 ms coalescing window keeps control feedback responsive while
            # preventing a burst/stream of MIDI packets from repainting the full bar.
            self.root.after(100, self._run_event_ui_refresh)
        except tk.TclError:
            self._ui_refresh_pending = False

    def _run_event_ui_refresh(self):
        self._ui_refresh_pending = False
        # Run the same state renderer without scheduling another periodic tick.
        self.tick(schedule_next=False)
        # A state transition may start/stop a countdown; recalculate its event-driven blink schedule.
        self._mode_flash_tick()

    def _apply_controller_batteries(self, levels, connected):
        self._battery_check_running = False
        self._controller_battery_levels = dict(levels)
        self._controller_connected_state = dict(connected)
        self._controller_battery_flash_tick()
        try:
            for name, label in self.controller_battery_labels.items():
                if connected.get(name, False):
                    value = levels.get(name)
                    label.configure(text=f'{name} {value}%' if value is not None else f'{name} --%')
                    label.place_configure()
                else:
                    label.place_forget()
        except tk.TclError:
            pass

    def _update_battery(self):
        """Restore the macOS computer battery percentage; refresh every 15 minutes."""
        try:
            result = subprocess.run(
                ['pmset', '-g', 'batt'], capture_output=True, text=True,
                timeout=1.5, check=False
            )
            import re
            match = re.search(r'(\d+)%', result.stdout)
            self.battery_label.configure(text=f'🔋 {int(match.group(1))}%' if match else '🔋 --%')
        except Exception:
            try:
                self.battery_label.configure(text='🔋 --%')
            except tk.TclError:
                return
        try:
            self.root.after(900000, self._update_battery)
        except tk.TclError:
            pass

    def _controller_battery_flash_tick(self, _from_timer=False):
        """Blink low-battery text only; stop scheduling callbacks when no battery is low."""
        if _from_timer:
            self._battery_flash_job = None
        elif self._battery_flash_job is not None:
            try:
                self.root.after_cancel(self._battery_flash_job)
            except tk.TclError:
                pass
            self._battery_flash_job = None
        low_labels = [
            label for name, label in self.controller_battery_labels.items()
            if self._controller_battery_levels.get(name) is not None
            and self._controller_battery_levels[name] < 35
        ]
        if not low_labels:
            self._battery_blink_on = False
            for label in self.controller_battery_labels.values():
                try:
                    label.configure(fg='#f0f4f8')
                except tk.TclError:
                    pass
            return
        self._battery_blink_on = not self._battery_blink_on
        for name, label in self.controller_battery_labels.items():
            try:
                level = self._controller_battery_levels.get(name)
                label.configure(fg=('#ff3030' if self._battery_blink_on else '#f0f4f8')
                                if level is not None and level < 35 else '#f0f4f8')
            except tk.TclError:
                pass
        try:
            self._battery_flash_job = self.root.after(500, lambda: self._controller_battery_flash_tick(True))
        except tk.TclError:
            self._battery_flash_job = None

    def _cycle_reminder(self, direction=1):
        visible = self._visible_message_indices()
        if not visible:
            return
        try: current = visible.index(self.reminder_index)
        except ValueError: current = 0
        self.reminder_index = visible[(current + direction) % len(visible)]
        self._show_reminder()

    def _auto_cycle_reminder(self):
        visible = self._visible_message_indices()
        if visible:
            try: current = visible.index(self.reminder_index)
            except ValueError: current = -1
            self.reminder_index = visible[(current + 1) % len(visible)]
        self._show_reminder()
        self._reminder_cycle_job = self.root.after(self._current_reminder_duration_ms(), self._auto_cycle_reminder)

    def _timer_press(self, event=None):
        # A native Tk double-click is reported on the second press.
        if getattr(event, 'type', None) == tk.EventType.ButtonPress and getattr(event, 'num', 1) == 1:
            pass
        self._timer_long_press_fired = False
        if self._timer_press_job is not None:
            try: self.root.after_cancel(self._timer_press_job)
            except tk.TclError: pass
        self._timer_press_job = self.root.after(700, self._timer_long_press)

    def _timer_double_press(self, _event=None):
        # Cancel pending single-click/long-press actions before opening editor.
        for attr in ('_timer_click_job', '_timer_press_job'):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        self._timer_long_press_fired = True
        self._timer_double_click_seen = True
        self._edit_session_timer()
        return 'break'

    def _timer_release(self, _event=None):
        if self._timer_double_click_seen:
            self._timer_double_click_seen = False
            return 'break'
        if self._timer_press_job is not None:
            try: self.root.after_cancel(self._timer_press_job)
            except tk.TclError: pass
            self._timer_press_job = None
        if self._timer_long_press_fired:
            return
        # Delay single-click action briefly so a double-click can be recognized.
        if self._timer_click_job is not None:
            try: self.root.after_cancel(self._timer_click_job)
            except tk.TclError: pass
            self._timer_click_job = None
        self._timer_click_job = self.root.after(260, self._session_click)

    def _timer_long_press(self):
        self._timer_press_job = None
        self._timer_long_press_fired = True
        if self._timer_click_job is not None:
            try: self.root.after_cancel(self._timer_click_job)
            except tk.TclError: pass
            self._timer_click_job = None
        self._edit_session_timer()

    def _reset_session_timer(self, _event=None):
        self.session_deadline = None
        self.session_remaining = self.default_timer_seconds
        self.set_timer_seconds = self.default_timer_seconds
        self.default_timer_seconds = self.message_settings.get('default_timer_seconds', self.default_timer_seconds)
        self.session_expired = False
        self._timer_flash_active = False
        self._timer_flash_on = False
        self._paint_tree(self.session_tile, '#393326')
        self.session_timer_label.configure(text=self._format_duration(self.set_timer_seconds), bg='#393326', fg=self.YELLOW)
        self._session_flash_tick()
        print(f'[SESSION TIMER] Reset to default {self._format_duration(self.default_timer_seconds)}; timer stopped.')
        return 'break'

    def _session_double_click(self, _event=None):
        # Legacy handler: double-click now opens the timer editor.
        return self._edit_session_timer(_event)

    def _session_click(self, _event=None):
        # Single click cycles: start -> pause -> resume. Double click opens timer editor.
        now = time.monotonic()
        if self.session_expired:
            self.set_timer_seconds = self.default_timer_seconds
            self.session_deadline = None
            self.session_remaining = self.set_timer_seconds
            self.session_expired = False
            self._paint_tree(self.session_tile, '#393326')
            self.session_timer_label.configure(text=self._format_duration(self.set_timer_seconds), bg='#393326', fg=self.YELLOW)
            print(f'[SESSION TIMER] Reset after expiry to default {self._format_duration(self.default_timer_seconds)}; timer stopped.')
            return
        if self.session_deadline is not None:
            self.session_remaining = max(0, self.session_deadline - now)
            self.session_deadline = None
            print(f'[SESSION TIMER] Paused at {self._format_duration(self.session_remaining)}.')
        elif self.session_remaining < self.set_timer_seconds:
            self.session_deadline = now + self.session_remaining
            print(f'[SESSION TIMER] Resumed with {self._format_duration(self.session_remaining)} remaining.')
        else:
            self.session_remaining = self.set_timer_seconds
            self.session_deadline = now + self.session_remaining
            print(f'[SESSION TIMER] Started {self._format_duration(self.session_remaining)} countdown.')
        self.session_timer_label.configure(text=self._format_duration(self.session_remaining), bg='#393326', fg=self.YELLOW)
        self._session_flash_tick()

    def _session_flash_tick(self, _from_timer=False):
        """Flash the timer tile every 500ms only during its final 3 seconds."""
        if _from_timer:
            self._session_flash_job = None
        elif self._session_flash_job is not None:
            try:
                self.root.after_cancel(self._session_flash_job)
            except tk.TclError:
                pass
            self._session_flash_job = None
        now = time.monotonic()
        remaining = None if self.session_deadline is None else self.session_deadline - now
        should_flash = (not self.session_expired and remaining is not None and 0 <= remaining <= 3.0)
        if should_flash:
            self._timer_flash_on = not self._timer_flash_on
            self._timer_flash_active = True
            color = '#ff4b4b' if self._timer_flash_on else '#393326'
            self._paint_tree(self.session_tile, color)
            delay_ms = 500
        else:
            if self._timer_flash_active:
                self._timer_flash_active = False
                self._timer_flash_on = False
                color = self.ORANGE if self.session_expired else '#393326'
                self._paint_tree(self.session_tile, color)
            # Sleep until 3 seconds remain; no polling while timer is stopped/paused.
            delay_ms = max(1, int((remaining - 3.0) * 1000)) if remaining is not None and remaining > 3.0 else None
        if delay_ms is not None:
            try:
                self._session_flash_job = self.root.after(delay_ms, lambda: self._session_flash_tick(True))
            except tk.TclError:
                self._session_flash_job = None

    def _mode_flash_tick(self, _from_timer=False):
        """Flash only the status-tile background in the final 3 seconds, every 0.5 s."""
        try:
            if _from_timer:
                self._mode_flash_job = None
            elif self._mode_flash_job is not None:
                try:
                    self.root.after_cancel(self._mode_flash_job)
                except tk.TclError:
                    pass
                self._mode_flash_job = None
            c = self.controller
            now = time.monotonic()
            with c.lock:
                fade = c.fade
                g2m_active = c.g2m_repeat_active or c.tb_repeat_active
                g2m_until = max(c.g2m_repeat_until, c.tb_repeat_until)
                lockout_until = c.lockout_until
                scenario_busy = c.scenario_busy
                active = fade.active
                quick_p = fade.short_action_active
                quick_down = fade.down_sequence_active
                started_at = fade.quick_started_at if (quick_p or quick_down) else fade.started_at
                special_until = fade.special_window_until
                fade_started_at = fade.fade_started_at
            remaining = None
            if active:
                if quick_p or quick_down:
                    base = self.GREEN_ON if quick_p else self.BLUE_ON
                    quick_started = fade.quick_started_at or started_at
                    remaining = max(0.0, SHORT_FADE_SECONDS - (now - quick_started))
                elif fade_started_at > 0:
                    base = self.RED
                    remaining = max(0.0, FULL_FADE_SECONDS - (now - fade_started_at))
                elif special_until > now:
                    base = self.PANEL
                    remaining = max(0.0, special_until - now)
                else:
                    base = self.PANEL
                    remaining = max(0.0, PRE_FADE_DELAY_SECONDS - (now - started_at))
            elif scenario_busy:
                base = self.RED
            elif g2m_active and g2m_until > now:
                base = self.PANEL
                remaining = max(0.0, g2m_until - now)
            elif lockout_until > now:
                base = self.PANEL
                remaining = max(0.0, lockout_until - now)
            else:
                base = self.PANEL
            self._mode_base_color = base
            should_flash = remaining is not None and 0.0 <= remaining <= 3.0
            if should_flash:
                self._mode_flash_on = not getattr(self, '_mode_flash_on', False)
                color = '#ff4b4b' if self._mode_flash_on else base
            else:
                self._mode_flash_on = False
                color = base
            # Only backgrounds change: never alter text foreground while flashing.
            self.mode_label.configure(bg=color)
            self.fade_tile.configure(bg=color)
            delay_ms = 500 if should_flash else (max(1, int((remaining - 3.0) * 1000)) if remaining is not None and remaining > 3.0 else None)
            if delay_ms is not None and self.root.winfo_exists():
                self._mode_flash_job = self.root.after(delay_ms, lambda: self._mode_flash_tick(True))
        except tk.TclError:
            return
        except Exception as exc:
            print(f'[TIMER FLASH WARNING] {type(exc).__name__}: {exc}')
            try:
                if self.root.winfo_exists():
                    self._mode_flash_job = self.root.after(500, lambda: self._mode_flash_tick(True))
            except tk.TclError:
                self._mode_flash_job = None
                return

    def _session_tile_normal_color(self):
        if self.session_expired:
            return self.ORANGE
        if self._timer_flash_active:
            return '#ff4b4b' if self._timer_flash_on else '#393326'
        return '#393326'

    def _edit_session_timer(self, _event=None):
        # Long press or double-click accepts digits only; guard against duplicate windows.
        existing = getattr(self, '_timer_editor', None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return 'break'
            except tk.TclError:
                pass
        win = tk.Toplevel(self.root)
        self._timer_editor = win
        win.title('Set timer for this run')
        win.configure(bg=self.PANEL)
        win.attributes('-topmost', True)
        win.resizable(False, False)
        tk.Label(win, text='Enter time using digits only (HHMM)' , bg=self.PANEL, fg=self.TEXT,
                 font=self.font).pack(padx=16, pady=(14, 6))
        tk.Label(win, text='Current default: ' + self._format_hms(self.default_timer_seconds), bg=self.PANEL, fg=self.TEXT).pack(padx=16, pady=(0, 4))
        entry = tk.Entry(win, width=8, justify='center', font=self.font)
        # Start empty: user enters a fresh duration.
        entry.pack(padx=16, pady=5)
        entry.focus_set()
        remember_var = tk.BooleanVar(value=bool(self.message_settings.get('remember_timer_default', True)))
        tk.Checkbutton(win, text='Save as default', variable=remember_var, bg=self.PANEL, fg=self.TEXT, selectcolor=self.PANEL, activebackground=self.PANEL, activeforeground=self.TEXT).pack()
        def save(_event=None):
            try:
                digits = entry.get().strip()
                if not digits.isdigit() or len(digits) not in (1, 2, 3, 4):
                    raise ValueError
                if len(digits) == 1:       # one digit = hours
                    hours, minutes = int(digits), 0
                elif len(digits) == 2:     # two digits = total minutes
                    hours, minutes = divmod(int(digits), 60)
                elif len(digits) == 3:     # three digits = total minutes, e.g. 090 = 90 minutes
                    hours, minutes = divmod(int(digits), 60)
                else:                      # four digits = HHMM
                    hours, minutes = int(digits[:2]), int(digits[2:])
                if hours > 99 or minutes > 59:
                    raise ValueError
                seconds = (hours * 60 + minutes) * 60
                if seconds < 60:
                    raise ValueError
            except ValueError:
                entry.configure(bg='#704040')
                return
            self.set_timer_seconds = seconds
            if remember_var.get():
                self.default_timer_seconds = seconds
                self.message_settings['default_timer_seconds'] = seconds
                self.message_settings['remember_timer_default'] = True
                self._save_message_settings()
            else:
                self.message_settings['remember_timer_default'] = False
                self._save_message_settings()
            self.session_remaining = seconds
            self.session_deadline = None
            self.session_expired = False
            self._timer_flash_active = False
            self._paint_tree(self.session_tile, '#393326')
            self.session_timer_label.configure(bg='#393326', fg=self.YELLOW, text=self._format_hms(seconds))
            self._session_flash_tick()
            print(f'[SESSION TIMER] One-off duration set to {self._format_hms(seconds)}; stopped, ready to start.')
            win.destroy()
            self._timer_editor = None
        win.protocol('WM_DELETE_WINDOW', lambda: (win.destroy(), setattr(self, '_timer_editor', None)))
        tk.Button(win, text='Save', command=save, font=self.small_font).pack(pady=(5, 14))
        entry.bind('<Return>', save)

    @staticmethod
    def _format_hms(seconds):
        seconds = max(0, int(seconds))
        return f'{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}' if seconds >= 3600 else f'{seconds // 60:02d}:{seconds % 60:02d}'

    @staticmethod
    def _format_duration(seconds):
        seconds = max(0, int(seconds))
        return f'{seconds // 60:02d}:{seconds % 60:02d}'

    def _update_clock(self):
        """Refresh the displayed clock once per minute; session timing remains separate."""
        try:
            clock_text = datetime.now().strftime('%H:%M')
            if clock_text != self._last_clock_text:
                self.clock_label.configure(text=clock_text)
                self._last_clock_text = clock_text
            self.root.after(30000, self._update_clock)
        except tk.TclError:
            return

    def tick(self, schedule_next=True):
        self.controller.ui_heartbeat = time.monotonic()
        # Handle global-shortcut requests on Tk's main thread (not the keyboard thread).
        if self.controller.restart_shortcut_requested:
            self.controller.restart_shortcut_requested = False
            self._confirm_restart(source='§')
        now = time.monotonic()
        if self.session_deadline is not None:
            delta = int(self.session_deadline - now)
            if delta >= 0:
                self.session_remaining = delta
                self.session_timer_label.configure(text=self._format_hms(delta))
            else:
                overtime = -delta
                elapsed_since_start = int(self.set_timer_seconds + overtime)
                self.session_expired = True
                self.session_tile.configure(bg=self.ORANGE)
                self.session_timer_label.configure(bg=self.ORANGE, fg='#171b22',
                                                   text='-' + self._format_hms(elapsed_since_start))

        c = self.controller
        # Debounce transient CoreMIDI/Bluetooth enumeration gaps. A brief BT MIDI
        # disconnect must not flash every tile red; show the error only after a
        # continuous fault for 5 seconds. A persistent fault remains visible.
        raw_fault = c.receiver_failed or (c.receiver_thread is not None and not c.receiver_thread.is_alive())
        raw_fault_text = 'MIDI RECEIVER STOPPED' if raw_fault else ''
        with c.port_status_lock:
            available_inputs, available_outputs, port_error = c.port_status_snapshot
        if port_error:
            raw_fault = True
            raw_fault_text = f'MIDI port check failed: {port_error}'
        elif available_inputs is not None and available_outputs is not None:
            if AIRSTEP_NAME not in available_inputs:
                self._missing_port_checks += 1
                # Snapshot is refreshed by the worker once per second; require
                # three consecutive missing snapshots before showing a fault.
                if self._missing_port_checks >= 3:
                    raw_fault = True
                    raw_fault_text = f'Missing input: {AIRSTEP_NAME}'
                else:
                    raw_fault = False if not c.receiver_failed and (c.receiver_thread is None or c.receiver_thread.is_alive()) else raw_fault
                    raw_fault_text = ''
            elif IAC_NAME not in available_outputs:
                self._missing_port_checks = 0
                raw_fault = True
                raw_fault_text = f'Missing output: {IAC_NAME}'
            else:
                self._missing_port_checks = 0

        if raw_fault:
            if self._fault_started_at is None:
                self._fault_started_at = now
                self._fault_message = raw_fault_text
                print(f'[MIDI STATUS] Fault detected; waiting 10 seconds before showing red: {raw_fault_text}')
            elif raw_fault_text:
                self._fault_message = raw_fault_text
            fault = (now - self._fault_started_at) >= 10.0
            if fault and not self._fault_was_visible:
                print(f'[MIDI STATUS] Persistent fault; bar switched to red: {self._fault_message}')
                self._fault_was_visible = True
        else:
            if self._fault_started_at is not None:
                print('[MIDI STATUS] MIDI ports/receiver healthy again; clearing fault state.')
            self._fault_started_at = None
            self._fault_message = ''
            fault = False
            self._fault_was_visible = False

        with c.lock:
            fade = c.fade
            active = fade.active
            started_at = fade.started_at
            special_until = fade.special_window_until
            fade_started_at = fade.fade_started_at
            quick = fade.short_action_active or fade.down_sequence_active
            scenario_busy = c.scenario_busy
            lockout_until = c.lockout_until
            g2m_repeat_active = c.g2m_repeat_active or c.tb_repeat_active
            g2m_repeat_until = max(c.g2m_repeat_until, c.tb_repeat_until)
            tb_repeat_active = c.tb_repeat_active
            last_midi = getattr(c, 'last_midi_in_display', '—')
            last_name = getattr(c, 'last_midi_name', '')
            last_midi_in_name = getattr(c, 'last_midi_in_name', '')

        if active and (fade.short_action_active or fade.down_sequence_active):
            quick_started_at = fade.quick_started_at or started_at
            remaining = max(0.0, SHORT_FADE_SECONDS - (now - quick_started_at))
            if fade.short_action_active:
                self.mode_label.configure(text=f'FAST FADE\n{remaining:.0f}', fg='#102916', font=self.font)
            else:
                self.mode_label.configure(text=f'LOOP FADER\n{remaining:.0f}', fg='#101d30', font=('Helvetica Neue', 14, 'bold'))
        elif active and now <= special_until:
            remaining = max(0.0, special_until - now)
            self.mode_label.configure(text=f'TT1  {remaining:.0f}\n{last_name or "Waiting for MIDI"}', fg=self.YELLOW, font=self.font)
        elif active and fade_started_at > 0:
            remaining = max(0.0, FULL_FADE_SECONDS - (now - fade_started_at))
            self.mode_label.configure(text=f'FADE  {remaining:.0f}\n{last_name or "Fade Out"}', fg='#171b22', font=self.font)
        elif active:
            remaining = max(0.0, PRE_FADE_DELAY_SECONDS - (now - started_at))
            self.mode_label.configure(text=f'TT1  {remaining:.0f}\n{last_name or "Waiting for MIDI"}', fg=self.YELLOW, font=self.font)
        elif g2m_repeat_active and g2m_repeat_until > now:
            remaining = max(0.0, g2m_repeat_until - now)
            self.mode_label.configure(text=f'{"TB" if tb_repeat_active else "CMD+ALT"}\n{remaining:.0f}', fg=self.TEXT, font=self.font)
        elif lockout_until > now:
            remaining = max(0.0, lockout_until-now)
            self.mode_label.configure(text=f'CANCEL\nFADEOUT', fg=self.YELLOW, font=self.font)
        elif scenario_busy:
            # Keep the red fadeout status visible until all volume restoration and HID work is complete.
            self.mode_label.configure(text='FADEOUT', fg='#171b22', font=self.font)
        else:
            self.mode_label.configure(text='READY', fg=self.YELLOW, font=self.font)

        missing_controllers = tuple(getattr(c, '_bt_missing_previous', ()) or ())
        disconnect_active = bool(missing_controllers)
        if disconnect_active:
            if self._disconnect_view_started_at <= 0.0:
                self._disconnect_view_started_at = now
                self._disconnect_show_error = True
            elif now - self._disconnect_view_started_at >= 3.0:
                intervals = int((now - self._disconnect_view_started_at) // 3.0)
                if intervals % 2:
                    self._disconnect_show_error = not self._disconnect_show_error
                self._disconnect_view_started_at += intervals * 3.0
        else:
            self._disconnect_view_started_at = 0.0
            self._disconnect_show_error = True
        show_disconnect = disconnect_active and self._disconnect_show_error
        if show_disconnect:
            missing_codes = []
            for missing_name in missing_controllers:
                upper = missing_name.upper()
                code = 'smc' if 'SMC' in upper else ('AIR' if 'AIRSTEP' in upper else ('p23' if 'P23' in upper else None))
                if code and code not in missing_codes:
                    missing_codes.append(code)
            # Stable compact format requested: abbreviated names on line 1,
            # and the same fixed disconnect status on line 2.
            self.midi_label.place_forget()
            self.disconnect_top.place(x=0, y=0, relwidth=1, relheight=0.5)
            self.disconnect_bottom.place(x=0, rely=0.5, relwidth=1, relheight=0.5)
            smc_missing = 'smc' in missing_codes
            air_missing = 'AIR' in missing_codes
            self.disconnect_top.configure(text=' '.join(missing_codes),
                                          bg='#ff1010' if smc_missing else '#242c32', fg='#f0f4f8')
            self.disconnect_bottom.configure(text='disconnected',
                                             bg='#ff1010' if air_missing else '#242c32', fg='#f0f4f8')
            # P23 has no specified red half; show its code without inventing one.
            if not (smc_missing or air_missing) and missing_codes:
                self.disconnect_top.configure(bg='#242c32')
                self.disconnect_bottom.configure(bg='#242c32')
            self._disconnect_display_active = True
        else:
            self.disconnect_top.place_forget()
            self.disconnect_bottom.place_forget()
            if self._disconnect_display_active:
                self.midi_label.place(relx=0.5, rely=0.5, anchor='center')
            self._disconnect_display_active = False

        fade_lit = (active and (fade_started_at > 0 or fade.short_action_active or fade.down_sequence_active)) or (scenario_busy and not active)
        # Background-only flash is handled by _mode_flash_tick at 500 ms intervals.
        if last_midi_in_name:
            midi_display = f'{last_midi_in_name}\n{last_midi}'
        else:
            midi_display = 'MIDI INPUT' if last_midi == '—' else f'\n{last_midi}\n'
        if not show_disconnect:
            self.midi_label.configure(text=midi_display)
        # Clear, high-visibility failure state across every status tile.
        tile_colors = [(self.reminder_tile, self.reminder_colors[self.reminder_index]), (self.session_tile, self._session_tile_normal_color()),
                       (self.fx_tile, '#ff1010' if c.fx_global_muted else {'normal': self.BLUE_ON, 'no_ng': self.GREEN_ON}.get(c.fx_state, self.BLUE_ON)),
                       *[(w, ((self.ORANGE if name in ('G2M', 'T.B.') else self.RED) if c.tile_states.get(name, False) else self.BLUE_OFF)) for name, w in self.status_tiles.items()],
                       *[(w, '#ff1010' if c.guitar_muted else self.BLUE_OFF) for w in self.effect_tiles.values()], (self.midi_tile, '#242c32'), (self.bpm_tile, '#242c32')]
        for tile_widget, normal_color in tile_colors:
            # Session and MIDI tiles have independent flash/fault overlays, so keep
            # repainting those; other tiles only need repainting when their color changes.
            if tile_widget in (self.session_tile, self.midi_tile) or self._last_tile_colors.get(tile_widget) != normal_color:
                self._paint_tree(tile_widget, normal_color)
                self._last_tile_colors[tile_widget] = normal_color
        # Status/effect label backgrounds are already updated by _paint_tree above.
        # The BPM label's background and foreground are static after construction.
        if fault and not getattr(c, '_bt_missing_previous', ()):
            # Generic port faults use one red tile only when no named controller is missing.
            self._paint_tree(self.midi_tile, self.RED)
            self.midi_label.configure(bg=self.RED, fg='#171b22', text='MIDI INPUT\nDisconnected')
        else:
            self._paint_tree(self.midi_tile, '#242c32')
            self.midi_label.configure(bg='#242c32', fg='#f0f4f8')
        # Reapply disconnect overlay after recursive tile painting, which otherwise
        # overwrites child-label backgrounds and makes the split red indicator vanish.
        if show_disconnect:
            smc_missing = any('SMC' in name.upper() for name in missing_controllers)
            air_missing = any('AIRSTEP' in name.upper() for name in missing_controllers)
            p23_missing = any('P23' in name.upper() for name in missing_controllers)
            missing_codes = []
            for missing_name in missing_controllers:
                upper = missing_name.upper()
                code = 'smc' if 'SMC' in upper else ('AIR' if 'AIRSTEP' in upper else ('p23' if 'P23' in upper else None))
                if code and code not in missing_codes:
                    missing_codes.append(code)
            self.midi_label.place_forget()
            self.disconnect_top.place(x=0, y=0, relwidth=1, relheight=0.5)
            self.disconnect_bottom.place(x=0, rely=0.5, relwidth=1, relheight=0.5)
            self.disconnect_top.configure(text=' '.join(missing_codes),
                bg='#ff1010' if (smc_missing or p23_missing) else '#242c32',
                fg='#f0f4f8', font=('Helvetica Neue', 14, 'bold'))
            self.disconnect_bottom.configure(text='disconnected',
                bg='#ff1010' if air_missing else '#242c32',
                fg='#f0f4f8', font=('Helvetica Neue', 14, 'bold'))
        # Preserve READY/status text and its normal state color during Bluetooth loss.
        if not scenario_busy and not active and not g2m_repeat_active and lockout_until <= now:
            self.mode_label.configure(text='READY', bg=self.PANEL, fg=self.YELLOW)

        if schedule_next:
            self.root.after(1000, self.tick)

    def _set_countdown_color(self, color):
        # The main status cell carries the controller mode; timer countdown is displayed in status text.
        self.mode_label.configure(fg=color)

class RawMacKeyboardListener:
    """Read physical key codes through Quartz only; never create NSEvent on a worker thread.

    This avoids the supplied macOS SIGTRAP in HIToolbox input-source lookup triggered
    by pynput's NSEvent.eventWithCGEvent conversion on its listener thread.
    """
    def __init__(self, controller):
        self.controller = controller
        self.thread = None
        self.run_loop = None
        self.ready = threading.Event()
        self.error = None
        self.tap = None

    def start(self):
        if Quartz is None or CoreFoundation is None:
            self.error = RuntimeError('Quartz/CoreFoundation PyObjC modules unavailable')
            print(f'[KEYBOARD WARNING] Global keyboard shortcuts unavailable: {self.error}')
            return
        self.thread = threading.Thread(target=self._run, name='QuartzKeyboardTap', daemon=True)
        self.thread.start()
        self.ready.wait(2.0)
        if self.error:
            print(f'[KEYBOARD WARNING] Native keyboard tap failed: {self.error}')

    def _callback(self, proxy, event_type, event, refcon):
        try:
            q = Quartz
            if event_type in (getattr(q, 'kCGEventTapDisabledByTimeout', -1), getattr(q, 'kCGEventTapDisabledByUserInput', -2)):
                if self.tap is not None: q.CGEventTapEnable(self.tap, True)
                return event
            keycode = int(q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode))
            flags = int(q.CGEventGetFlags(event))
            c = self.controller
            c._pressed_keys.clear()
            if flags & q.kCGEventFlagMaskControl: c._pressed_keys.add('ctrl')
            if flags & q.kCGEventFlagMaskAlternate: c._pressed_keys.add('alt')
            if flags & q.kCGEventFlagMaskShift: c._pressed_keys.add('shift')
            if event_type == q.kCGEventKeyDown:
                if keycode == 125:
                    c.on_key(keyboard.Key.down)
                elif keycode == 10:
                    c.on_key(type('PhysicalKey', (), {'vk': 10})())
            elif event_type == q.kCGEventKeyUp and keycode == 10:
                c.on_key_release(type('PhysicalKey', (), {'vk': 10})())
        except Exception as exc:
            self.controller._log_runtime_exception('Quartz keyboard callback', exc)
        return event

    def _run(self):
        try:
            q = Quartz
            mask = (1 << q.kCGEventKeyDown) | (1 << q.kCGEventKeyUp) | (1 << q.kCGEventFlagsChanged)
            self.tap = q.CGEventTapCreate(q.kCGSessionEventTap, q.kCGHeadInsertEventTap,
                q.kCGEventTapOptionListenOnly, mask, self._callback, None)
            if self.tap is None:
                raise RuntimeError('CGEventTapCreate returned None; grant Accessibility/Input Monitoring permission')
            source_factory = getattr(CoreFoundation, 'CFMachPortCreateRunLoopSource', None) or getattr(Quartz, 'CFMachPortCreateRunLoopSource', None)
            if source_factory is None:
                raise RuntimeError('CFMachPortCreateRunLoopSource is unavailable in this PyObjC build')
            source = source_factory(None, self.tap, 0)
            self.run_loop = CoreFoundation.CFRunLoopGetCurrent()
            CoreFoundation.CFRunLoopAddSource(self.run_loop, source, CoreFoundation.kCFRunLoopCommonModes)
            q.CGEventTapEnable(self.tap, True)
            self.ready.set()
            CoreFoundation.CFRunLoopRun()
        except Exception as exc:
            self.error = exc
            self.ready.set()

    def stop(self):
        try:
            if self.run_loop is not None: CoreFoundation.CFRunLoopStop(self.run_loop)
        except Exception: pass


class Controller:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.fade = FadeState()
        self.lockout_until = 0.0
        self.awaiting_tt = True
        self.scenario_busy = False
        # SBP22 is available before TT1 and after TT1 scenarios that do not
        # finish with the full fade-out. A completed full fade-out keeps it disabled.
        self.last_tt1_full_fade = False
        self.in_port = None
        self.out_port = None
        self.generation = 0
        # G2M repeat-window state must exist before the first MIDI event arrives.
        self.g2m_repeat_active = False
        self.g2m_repeat_until = 0.0
        self.g2m_repeat_generation = 0
        self.tb_repeat_active = False
        self.tb_repeat_until = 0.0
        self.tb_repeat_generation = 0
        self.silent = False
        self.tile_states = {name: False for name in ('FX', 'G2M', 'T.B.', 'MM', 'Bvox', 'Solos')}
        self.fx_state = 'normal'
        self.fx_global_muted = False
        self.guitar_muted = False
        self._guitar_mute_button_down = False
        self.smc_in_port = None
        self.smc_receiver_thread = None
        self.iac_state_in_port = None
        self.iac_state_receiver_thread = None
        self.last_midi_display = "—"  # legacy outgoing display retained for diagnostics
        self.last_midi_in_display = "—"
        self.last_midi_in_name = ""
        self.last_midi_in_at = 0.0
        self.last_midi_name = ""
        self.receiver_thread = None
        self.receiver_failed = False
        self.port_failure = ''
        self.restart_requested = False
        self.restart_source = ''
        self.restart_shortcut_requested = False
        self._restart_shortcut_latched = False
        self._pressed_keys = set()
        self._bt_monitor_last_at = 0.0
        self._bt_monitor_previous = None
        self._bt_missing_previous = None
        # MIDI/CoreMIDI enumeration must never run on Tk's UI thread.
        self.port_status_lock = threading.Lock()
        self.port_status_snapshot = (None, None, None)
        self.port_status_thread = None
        self.ui_heartbeat = time.monotonic()
        self.fault_detected_at = None
        # Startup layout is always the split/compact JamZone + SBP layout.
        self.layout_compact = True
        self._layout_toggle_busy = False
        self._layout_lock = threading.Lock()
        self._layout_toggle_latched = False
        self._bar_visibility_latched = False
        self._pending_bar_visibility_toggle = False
        self.root = None
        self.floating_bar = None
        self._watchdog_thread = None

    def _osascript(self, script, label):
        try:
            result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=4)
            if result.returncode != 0:
                print(f'[WINDOW CONTROL] {label} failed: {result.stderr.strip()}')
                return False
            return True
        except Exception as exc:
            print(f'[WINDOW CONTROL] {label} failed: {type(exc).__name__}: {exc}')
            return False

    def toggle_jamzone_sbp_layout(self):
        with self._layout_lock:
            if self._layout_toggle_busy: return
            self._layout_toggle_busy = True
        try:
            # Dimensions are captured from Tk on the main thread during startup.
            screen_width = int(self.screen_width)
            screen_height = int(self.screen_height)
            if not self.layout_compact:
                # Compact JamZone; preserve height and keep its bottom edge fixed.
                script = ('tell application "System Events"\n'
                          'tell process "Jamzone"\n'
                          'set currentSize to size of window 1\n'
                          'set newHeight to item 2 of currentSize\n'
                          'set size of window 1 to {1, newHeight}\n'
                          'set position of window 1 to {0, ' + str(screen_height) + ' - newHeight}\n'
                          'end tell\nend tell')
                ok = self._osascript(script, 'JamZone compact-width layout')
                if ok:
                    self.layout_compact = True
                    self._osascript('tell application "SongBookPro" to activate', 'Bring SongBookPro forward after compact layout')
                    print('[LAYOUT] Compact JamZone + SBP foreground requested; bottom edge anchored.')
            else:
                # Full-width JamZone; preserve height and keep the same bottom edge.
                script = ('tell application "System Events"\n'
                          'tell process "Jamzone"\n'
                          'set currentSize to size of window 1\n'
                          'set newHeight to item 2 of currentSize\n'
                          'set size of window 1 to {' + str(screen_width) + ', newHeight}\n'
                          'set position of window 1 to {0, ' + str(screen_height) + ' - newHeight}\n'
                          'end tell\nend tell')
                ok = self._osascript(script, 'JamZone full-width layout')
                if ok:
                    self.layout_compact = False
                    self._osascript('tell application "System Events" to tell process "Jamzone" to set frontmost to true', 'Bring JamZone forward')
                    print('[LAYOUT] Full-width JamZone requested; bottom edge anchored.')
        finally:
            with self._layout_lock: self._layout_toggle_busy = False

    def _compact_cc22_action(self):
        # CC22 activates JamZone, sends P, then returns focus to SBP in either layout.
        activated = self._osascript('tell application "System Events" to tell process "Jamzone" to set frontmost to true', 'Activate JamZone for CC22')
        if not activated:
            return
        time.sleep(0.3)
        # Use the existing System Events + physical keycode path. Do not
        # instantiate pynput.keyboard.Controller in this background thread: the
        # macOS crash report showed a SIGTRAP in HIToolbox keyboard-layout lookup.
        if self.send_p():
            print('[LAYOUT] CC22 sent P to JamZone.')
        else:
            print('[LAYOUT] JamZone P key failed through System Events.')
        time.sleep(0.3)
        self._osascript('tell application "SongBookPro" to activate', 'Return to SongBookPro after CC22')

    def _jamzone_only_cc22_action(self):
        # In full-width JamZone-only layout, CC22 must not bring SongBookPro forward.
        activated = self._osascript('tell application "System Events" to tell process "Jamzone" to set frontmost to true', 'Activate JamZone for CC22 in JamZone-only layout')
        if not activated:
            return
        time.sleep(0.3)
        if self.send_p():
            print('[LAYOUT] CC22 sent P to JamZone; keeping JamZone in front (SBP22 inactive in JamZone-only layout).')
        else:
            print('[LAYOUT] JamZone P key failed through System Events; keeping JamZone in front.')

    def _ensure_smc_receiver(self, inputs):
        """Close stale SMC ports and reliably reopen the receiver after Bluetooth reconnects."""
        candidates = [name for name in inputs if 'SMC-Mixer Bluetooth' in name]
        current_name = getattr(self, '_smc_input_name', None)
        thread_alive = bool(self.smc_receiver_thread and self.smc_receiver_thread.is_alive())
        if not candidates:
            if self.smc_in_port is not None:
                try:
                    self.smc_in_port.close()
                except Exception:
                    pass
            self.smc_in_port = None
            self.smc_receiver_thread = None
            self._smc_input_name = None
            return
        candidate = candidates[0]
        # Reuse only a still-open receiver for the same CoreMIDI endpoint.
        if self.smc_in_port is not None and thread_alive and current_name == candidate:
            return
        if self.smc_in_port is not None:
            try:
                self.smc_in_port.close()
            except Exception:
                pass
        self.smc_in_port = None
        self.smc_receiver_thread = None
        try:
            port = mido.open_input(candidate)
            self.smc_in_port = port
            self._smc_input_name = candidate
            thread = threading.Thread(
                target=self._smc_receive_loop, args=(port,), name='SMCStateMonitor', daemon=True
            )
            self.smc_receiver_thread = thread
            thread.start()
            print(f'[BT MIDI RECOVERY] Opened SMC state input: {candidate}')
        except Exception as exc:
            self.smc_in_port = None
            self.smc_receiver_thread = None
            self._smc_input_name = None
            print(f'[BT MIDI RECOVERY] Could not open SMC state input: {type(exc).__name__}: {exc}; retrying.')

    def _battery_scheduler_loop(self):
        """Refresh SMC/P23 battery levels every 15 minutes off the UI thread.

        AIRSTEP and SMC MIDI-port presence are monitored independently every second.
        P23 is not a CoreMIDI port, so its connected/disconnected state is refreshed
        by the Bluetooth battery/presence scan and may lag until that scan runs.
        """
        while not self.stop_event.wait(900):
            if not self.stop_event.is_set():
                bar = getattr(self, 'floating_bar', None)
                if bar is not None:
                    bar._update_controller_batteries()

    def _port_status_monitor_loop(self):
        """Refresh CoreMIDI port snapshots away from Tk's UI thread."""
        while not self.stop_event.is_set():
            try:
                inputs = tuple(mido.get_input_names())
                outputs = tuple(mido.get_output_names())
                snapshot = (inputs, outputs, None)
            except Exception as exc:
                inputs = outputs = None
                snapshot = (None, None, f'{type(exc).__name__}')
            with self.port_status_lock:
                self.port_status_snapshot = snapshot
            try:
                self._ensure_smc_receiver(inputs if inputs is not None else ())
                self._log_bluetooth_midi_status(inputs=inputs if inputs is not None else ())
            except Exception as exc:
                print(f'[BT MIDI MONITOR] Status logging failed: {type(exc).__name__}: {exc}')
            # This detects AIRSTEP and SMC promptly. P23 presence comes from the slower
        # Bluetooth scan because it does not appear in CoreMIDI input enumeration.
        self.stop_event.wait(1.0)

    def _watchdog_loop(self):
        while not self.stop_event.wait(5.0):
            now = time.monotonic()
            stale = now - self.ui_heartbeat
            runtime_fault = self.fault_detected_at is not None and now - self.fault_detected_at >= 2.0
            if stale > 4.0 or runtime_fault:
                reason = f'UI heartbeat stalled for {stale:.1f}s' if stale > 4.0 else 'runtime exception detected'
                _write_crash_record(f'WATCHDOG: {reason}; full process restart')
                # Capture every Python thread's current stack BEFORE execv replaces
                # this process. This identifies what the UI thread is blocked in.
                # Diagnostics only: no MIDI messages, routing, or UI behavior change.
                try:
                    handle = _CRASH_LOG_HANDLE
                    if handle is not None:
                        with _CRASH_LOG_LOCK:
                            handle.write(
                                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                                f"THREAD STACK SNAPSHOT BEFORE RESTART ({reason})\n"
                            )
                            faulthandler.dump_traceback(file=handle, all_threads=True)
                            handle.flush()
                            os.fsync(handle.fileno())
                except Exception as diagnostic_exc:
                    _write_crash_record(
                        f'WATCHDOG STACK SNAPSHOT FAILED: {type(diagnostic_exc).__name__}: {diagnostic_exc}'
                    )
                print(f'[WATCHDOG] {reason}; restarting. Bluetooth disconnects are not restart triggers.')
                os.environ['MAINSTAGE_JAMZONE_RESTART_PENDING'] = '1'
                os.execv(sys.executable, [sys.executable] + sys.argv)

    # AIRSTEP CH16 CC41 is the MM command; its absolute display state must be mirrored to Bus 2 once the MainStage mapping is configured.
    # MIDI Monitor labels use C-2 for note number 0: D-2=2, C-2=0,
    # D#-2=3, B-2=11, and D-1=14.
    SMC_STATE_NOTES = {2: 'G2M', 0: 'T.B.', 3: 'Bvox', 11: 'Solos', 14: 'FX'}
    GUITAR_MUTE_NOTE = 18  # F#-1 in MIDI Monitor's note naming
    BUS2_CC_TO_TILE = {42: 'G2M', 45: 'T.B.', 41: 'MM', 39: 'Bvox', 40: 'Solos'}

    def _record_smc_state(self, msg):
        if not hasattr(msg, 'channel') or msg.channel != 0:
            return
        if msg.type not in ('note_on', 'note_off'):
            return
        velocity = getattr(msg, 'velocity', 0)
        if getattr(msg, 'note', -1) == 94 and msg.type == 'note_on' and not self._layout_toggle_latched:
            self._layout_toggle_latched = True
            threading.Thread(target=self.toggle_jamzone_sbp_layout, name='JamZoneLayoutToggle', daemon=True).start()
        elif getattr(msg, 'note', -1) == 94 and (msg.type == 'note_off' or (msg.type == 'note_on' and velocity == 0)):
            self._layout_toggle_latched = False
        is_on = msg.type == 'note_on' and velocity > 0
        note_number = getattr(msg, 'note', -1)
        # SMC CH1 Note 93 toggles bar visibility; it never stops MIDI or the controller.
        if note_number == 93:
            if is_on and not self._bar_visibility_latched:
                self._bar_visibility_latched = True
                root = getattr(self, 'root', None)
                if root is None:
                    self._pending_bar_visibility_toggle = not self._pending_bar_visibility_toggle
                    print('[BAR VISIBILITY] Tk not ready; queued Note 93 toggle.')
                else:
                    try:
                        root.after(0, self.floating_bar._toggle_bar_visibility)
                    except (AttributeError, tk.TclError):
                        self._pending_bar_visibility_toggle = not self._pending_bar_visibility_toggle
                        print('[BAR VISIBILITY] Tk scheduling unavailable; queued Note 93 toggle.')
            elif not is_on:
                self._bar_visibility_latched = False
            return
        if VERBOSE_MIDI_LOGGING:
            print(
                f'[SMC RAW] type={msg.type} channel={msg.channel + 1} '
                f'note={note_number if note_number >= 0 else "n/a"} velocity={velocity} '
                f'interpretation={"ON" if is_on else "OFF"}'
            )
        # Once Bus 2 is available, it is the authoritative MainStage state mirror.
        # Do not let the separate SMC Bluetooth input echo overwrite that state with
        # a second/return-path message. This is the same principle that fixed G2M,
        # applied to every mapped state button in the bar.
        if self.iac_state_in_port is not None and note_number in {0, 2, 3, 11, 14, 22}:
            if VERBOSE_MIDI_LOGGING:
                print(f'[SMC STATE IGNORED] note={note_number}; authoritative state is read from IAC Bus 2')
            return
        if note_number == self.GUITAR_MUTE_NOTE:
            velocity = getattr(msg, 'velocity', 0) if msg.type == 'note_on' else 0
            pressed = msg.type == 'note_on' and velocity > 0
            with self.lock:
                if pressed and not self._guitar_mute_button_down:
                    self.guitar_muted = not self.guitar_muted
                    self._guitar_mute_button_down = True
                    changed = True
                elif not pressed:
                    self._guitar_mute_button_down = False
                    changed = False
                else:
                    changed = False
                muted = self.guitar_muted
            if changed:
                print(f'[SMC STATE] GUITAR_MUTE={"ON" if muted else "OFF"}')
            return
        if getattr(msg, 'note', -1) == 22:
            with self.lock:
                changed = self.fx_global_muted != is_on
                self.fx_global_muted = is_on
                self.fx_state = 'muted' if is_on else ('normal' if self.tile_states.get('FX', False) else 'no_ng')
            if changed:
                print(f'[SMC STATE] FX_GLOBAL_MUTE={"ON" if is_on else "OFF"}')
            return
        tile_name = self.SMC_STATE_NOTES.get(getattr(msg, 'note', -1))
        if tile_name is None:
            return
        with self.lock:
            changed = self.tile_states.get(tile_name) != is_on
            self.tile_states[tile_name] = is_on
            if tile_name == 'FX' and not self.fx_global_muted:
                new_fx_state = 'normal' if is_on else 'no_ng'
                changed = changed or self.fx_state != new_fx_state
                self.fx_state = new_fx_state
        if changed:
            print(f'[SMC STATE] {tile_name}={"ON" if is_on else "OFF"} from {msg.type} note={msg.note} velocity={getattr(msg, "velocity", 0)}')
            bar = getattr(self, 'floating_bar', None)
            if bar is not None:
                bar.request_ui_refresh()

    def _log_bluetooth_midi_status(self, force=False, inputs=None):
        """Log AIRSTEP and SMC Bluetooth MIDI port/receiver transitions for diagnosis."""
        now = time.monotonic()
        if not force and now - self._bt_monitor_last_at < 1.0:
            return
        self._bt_monitor_last_at = now
        try:
            if inputs is None:
                inputs = mido.get_input_names()
            airstep_present = AIRSTEP_NAME in inputs
            smc_matches = [name for name in inputs if 'SMC-Mixer Bluetooth' in name]
            smc_present = bool(smc_matches)
            airstep_thread_alive = bool(self.receiver_thread and self.receiver_thread.is_alive())
            smc_thread_alive = bool(self.smc_receiver_thread and self.smc_receiver_thread.is_alive())
            bar = getattr(self, 'floating_bar', None)
            controller_state = getattr(bar, '_controller_connected_state', {}) if bar is not None else {}
            snapshot = (airstep_present, smc_present, airstep_thread_alive, smc_thread_alive,
                        tuple(smc_matches), tuple(inputs), controller_state.get('P23'))
            if force or snapshot != self._bt_monitor_previous:
                print(
                    '[BT MIDI MONITOR] '
                    f'AIRSTEP_port={"PRESENT" if airstep_present else "MISSING"}; '
                    f'AIRSTEP_receiver={"RUNNING" if airstep_thread_alive else "NOT RUNNING"}; '
                    f'SMC_port={"PRESENT" if smc_present else "MISSING"}; '
                    f'SMC_receiver={"RUNNING" if smc_thread_alive else "NOT RUNNING"}; '
                    f'SMC_names={smc_matches}; available_inputs={inputs}'
                )
                if self._bt_monitor_previous is not None:
                    prev = self._bt_monitor_previous
                    if prev[0] and not airstep_present:
                        print('[BT MIDI DISCONNECT] AIRSTEP Bluetooth input disappeared from CoreMIDI enumeration.')
                    elif not prev[0] and airstep_present:
                        print('[BT MIDI RECONNECT] AIRSTEP Bluetooth input reappeared in CoreMIDI enumeration.')
                    if prev[1] and not smc_present:
                        print('[BT MIDI DISCONNECT] SMC-Mixer Bluetooth input disappeared from CoreMIDI enumeration.')
                    elif not prev[1] and smc_present:
                        print('[BT MIDI RECONNECT] SMC-Mixer Bluetooth input reappeared in CoreMIDI enumeration.')
                    if prev[2] and not airstep_thread_alive:
                        print('[BT MIDI RECEIVER STOPPED] AIRSTEP receive thread is no longer running.')
                    if prev[3] and not smc_thread_alive:
                        print('[BT MIDI RECEIVER STOPPED] SMC state-monitor thread is no longer running.')
                missing = []
                if not airstep_present:
                    missing.append('AIRSTEP Bluetooth')
                if not smc_present:
                    missing.append('SMC-Mixer Bluetooth')
                bar = getattr(self, 'floating_bar', None)
                controller_state = getattr(bar, '_controller_connected_state', {}) if bar is not None else {}
                p23_connected = controller_state.get('P23')
                if p23_connected is False:
                    missing.append('P23')
                missing_tuple = tuple(missing)
                if missing_tuple != getattr(self, '_bt_missing_previous', None):
                    if missing_tuple:
                        print(', '.join(missing_tuple) + '\ndisconnect')
                    elif getattr(self, '_bt_missing_previous', None):
                        print('[BT RECONNECT] All monitored Bluetooth controllers are present.')
                    self._bt_missing_previous = missing_tuple
                self._bt_monitor_previous = snapshot
        except Exception as exc:
            print(f'[BT MIDI MONITOR ERROR] {type(exc).__name__}: {exc}')

    def _smc_receive_loop(self, port=None):
        port = port or self.smc_in_port
        if port is None:
            return
        try:
            for msg in port:
                if self.stop_event.is_set():
                    break
                self._record_smc_state(msg)
        except Exception as exc:
            if not self.stop_event.is_set():
                self._log_runtime_exception('SMC state monitor', exc)
                print(f'[SMC STATE WARNING] State monitor stopped: {type(exc).__name__}: {exc}')

    def _log_iac_bus2_message(self, msg):
        """Log each Bus 2 input in a MIDI-Monitor-like format; read-only diagnostic."""
        fields = [f'type={msg.type}']
        if hasattr(msg, 'channel'):
            fields.append(f'channel={msg.channel + 1}')
        if hasattr(msg, 'note'):
            fields.append(f'note={msg.note}')
        if hasattr(msg, 'velocity'):
            fields.append(f'velocity={msg.velocity}')
        if hasattr(msg, 'control'):
            fields.append(f'control={msg.control}')
        if hasattr(msg, 'value'):
            fields.append(f'value={msg.value}')
        print('[IAC BUS2 RAW] ' + ' '.join(fields) + f' raw={msg!s}')

    def _iac_bus2_receive_loop(self):
        try:
            for msg in self.iac_state_in_port:
                if self.stop_event.is_set():
                    break
                # Bus 2 is a state mirror. Refresh the UI only when a recognized
                # state actually changes; ignore unrelated and duplicate packets.
                state_changed = False
                if msg.type == 'control_change' and msg.channel == 14 and msg.control in self.BUS2_CC_TO_TILE and msg.value in (0, 127):
                    tile_name = self.BUS2_CC_TO_TILE[msg.control]
                    active = (msg.value == 127)
                    with self.lock:
                        changed = self.tile_states.get(tile_name) != active
                        self.tile_states[tile_name] = active
                    state_changed = state_changed or changed
                    if changed:
                        print(f'[BAR STATE] {tile_name}={"ON" if active else "OFF"} from IAC Bus 2 CH15 CC{msg.control} value={msg.value}')
                # FX sidechain bypass state mirror: CH15 CC43, 127=bypassed, 0=normal.
                if msg.type == 'control_change' and msg.channel == 14 and msg.control == 43 and msg.value in (0, 127):
                    with self.lock:
                        new_fx_state = 'no_ng' if msg.value == 127 else 'normal'
                        changed = (not self.fx_global_muted and self.fx_state != new_fx_state)
                        if not self.fx_global_muted:
                            self.fx_state = new_fx_state
                    state_changed = state_changed or changed
                    if changed:
                        print(f'[BAR STATE] FX_SIDECHAIN_BYPASS={"ON" if msg.value == 127 else "OFF"} from IAC Bus 2 CH15 CC43 value={msg.value}')
                # MainStage can mirror absolute button states to Bus 2.
                # Interpret note_on velocity 0 as OFF, exactly like MIDI Monitor.
                if msg.type in ('note_on', 'note_off') and hasattr(msg, 'note'):
                    velocity = getattr(msg, 'velocity', 0) if msg.type == 'note_on' else 0
                    is_on = msg.type == 'note_on' and velocity > 0
                    note_to_tile = {2: 'G2M', 0: 'T.B.', 3: 'Bvox', 11: 'Solos'}
                    if msg.note in note_to_tile:
                        tile_name = note_to_tile[msg.note]
                        with self.lock:
                            changed = self.tile_states.get(tile_name) != is_on
                            self.tile_states[tile_name] = is_on
                        state_changed = state_changed or changed
                        if changed:
                            print(f'[BAR STATE] {tile_name}={"ON" if is_on else "OFF"} from IAC Bus 2 note={msg.note} velocity={velocity}')
                    elif msg.note == 22:
                        with self.lock:
                            changed = self.fx_global_muted != is_on
                            self.fx_global_muted = is_on
                        state_changed = state_changed or changed
                        if changed:
                            print(f'[BAR STATE] FX_GLOBAL_MUTE={"ON" if is_on else "OFF"} from IAC Bus 2 note=22 velocity={velocity}')
                    elif msg.note == 14:
                        with self.lock:
                            new_fx_state = 'normal' if is_on else 'no_ng'
                            changed = self.fx_state != new_fx_state
                            self.fx_state = new_fx_state
                        state_changed = state_changed or changed
                        if changed:
                            print(f'[BAR STATE] FX_NG={"ON" if is_on else "OFF"} from IAC Bus 2 note=14 velocity={velocity}')
                    elif msg.note == 18:
                        pressed = msg.type == 'note_on' and velocity > 0
                        with self.lock:
                            if pressed and not self._guitar_mute_button_down:
                                self.guitar_muted = not self.guitar_muted
                                self._guitar_mute_button_down = True
                                changed = True
                            elif not pressed:
                                self._guitar_mute_button_down = False
                                changed = False
                            else:
                                changed = False
                            muted = self.guitar_muted
                        state_changed = state_changed or changed
                        if changed:
                            print(f'[BAR STATE] GUITAR_MUTE={"ON" if muted else "OFF"} from IAC Bus 2 press edge=True; release does not change mute state')
                if state_changed:
                    bar = getattr(self, 'floating_bar', None)
                    if bar is not None:
                        bar.request_ui_refresh()
        except Exception as exc:
            if not self.stop_event.is_set():
                self._log_runtime_exception('IAC Bus 2 diagnostic input', exc)
                print(f'[IAC BUS2 WARNING] Receiver stopped: {type(exc).__name__}: {exc}')

    def _record_midi_in(self, msg):
        """Store the latest incoming command and its friendly name for the status bar."""
        self.last_midi_in_name = ""
        self.last_midi_in_at = time.monotonic()
        if msg.type == "control_change":
            self.last_midi_in_display = f"ch{msg.channel + 1:02d} cc{msg.control}\nv{msg.value}"
            names = {
                (15, 42): "G2M", (15, 41): "MM",
                (15, 39): "Bvox", (15, 40): "Solos",
                (15, 43): "FX Sidechain", (15, 44): "Tap Tempo", (15, 45): "TalkBack",
            }
            self.last_midi_in_name = names.get((msg.channel, msg.control), "")
        elif msg.type in ("note_on", "note_off"):
            self.last_midi_in_display = f"ch{msg.channel + 1:02d} {msg.type} n{msg.note}\nv{msg.velocity}"
        else:
            self.last_midi_in_display = f"ch{getattr(msg, 'channel', 0) + 1:02d} {msg.type}"

    def _record_midi_out(self, msg):
        if msg.type == "control_change":
            self.last_midi_display = f"ch{msg.channel + 1:02d} cc{msg.control} v{msg.value}"
            names = {
                (14, 44): "TT1", (14, 42): "G2M", (14, 22): "Quick Fade / P",
                (14, 20): "Master Fade", (14, 21): "Quick Fade", (14, 60): "FX Sidechain",
            }
            self.last_midi_name = names.get((msg.channel, msg.control), f"CC{msg.control}")

    def send_iac_message(self, msg, context=""):
        """Send every MIDI message to IAC Bus 1 on human MIDI channel 15."""
        if hasattr(msg, "channel"):
            msg = msg.copy(channel=14)  # mido is zero-based; 14 = MIDI CH15
        self.out_port.send(msg)
        self._record_midi_out(msg)
        suffix = f" ({context})" if context else ""
        print(f"[MIDI SENT -> IAC] {format_midi_message(msg)}{suffix}")
        return msg

    def send_cc(self, channel, control, value):
        value = max(0, min(127, int(round(value))))
        msg = mido.Message(
            "control_change", channel=14, control=control, value=value
        )
        self.send_iac_message(msg)

    def startup_button_sync(self, root, index=0):
        """Pulse each existing status-button command twice so MainStage re-emits Bus 2 state."""
        # Use only mappings actually present in this script. CC43 is the Bus 2
        # FX-sidechain state mirror; CC60 is an action command, not the state mapping.
        # FX Mute is Note 22. No DLY–IR command mappings exist here.
        commands = (
            ("cc", 42, 127, "G2M"),
            ("cc", 45, 127, "T.B."),
            ("cc", 41, 127, "MM"),
            ("cc", 39, 127, "Bvox"),
            ("cc", 40, 127, "Solos"),
            # Toggle global FX mute before requesting the sidechain mirror.
            # The Bus 2 listener intentionally gives global mute display priority,
            # so sending CC43 while FX Mute is active can leave the sidechain tile stale.
            ("note_on", 22, 127, "FX Mute"),
            ("note_on", 18, 127, "Guitar Mute state"),
            ("cc", 43, 127, "FX Sidechain"),
        )
        sequence = commands + commands
        if self.stop_event.is_set():
            return
        if index >= len(sequence):
            print('[STARTUP SYNC] Completed two passes: CH15 CC42, CC45, CC41, CC39, CC40, Note 22, CC43.')
            return
        kind, number, value, label = sequence[index]
        pass_number = 1 if index < len(commands) else 2
        if kind == "cc":
            msg = mido.Message("control_change", channel=14, control=number, value=value)
        else:
            msg = mido.Message("note_on", channel=14, note=number, velocity=value)
        print(f'[STARTUP SYNC] Pass {pass_number}/2: sending IAC CH15 {label} {kind} {number} value={value}')
        self.send_iac_message(msg, context=f"startup sync {label}")
        # Space commands so MainStage has time to process each trigger and emit its state.
        root.after(150, lambda: self.startup_button_sync(root, index + 1))

    def send_volume_cc(self, control, value):
        value = max(0, min(127, int(round(value))))
        self.send_cc(VOLUME_CHANNEL, control, value)
        self.silent = (value == 0)
        if self.fade.active:
            self.fade.current_value = value

    def send_volume(self, value):
        # Full 15-second fade uses IAC CH15 CC20.
        self.send_volume_cc(VOLUME_CC, value)

    def send_quick_volume(self, value):
        # Both 1-second fade scenarios use IAC CH15 CC21.
        self.send_volume_cc(QUICK_FADE_CC, value)

    def send_effect_cc(self, phase):
        # IAC CH15 CC60 value 0 toggles the effect/sidechain state for quick fades.
        print(f"[SIDECHAIN] sending IAC CH15 CC60 value=0 ({phase})")
        self.send_cc(VOLUME_CHANNEL, EFFECT_CC, 0)

    def activate_jamzone_and_wait(self):
        if not self.activate_jamzone():
            return False
        time.sleep(0.5)
        return True

    def stop_playback_in_jamzone(self):
        # HID keystrokes are sent only after JamZone is explicitly frontmost
        # and has had half a second to receive them.
        if self.activate_jamzone_and_wait():
            self.send_p()
            return True
        return False

    def send_key(self, key):
        """Send HID-style keystrokes through System Events, not pynput.Controller.
        pynput's macOS layout lookup can call HIToolbox TSM APIs from a worker
        thread; the supplied crash report shows a queue assertion in that path.
        """
        if key == keyboard.Key.up:
            script = 'tell application "System Events" to key code 126'
            label = 'up'
        elif isinstance(key, str) and key.lower() in ("p", "l"):
            # Physical US-layout key positions: P=35, L=37. key code avoids
            # translating characters through the currently selected Hebrew/English layout.
            keycode = {"p": 35, "l": 37}[key.lower()]
            script = f'tell application "System Events" to key code {keycode}'
            label = key.lower() + " (physical keycode)"
        else:
            print(f"[HID BLOCKED] Unsupported key request: {key!r}")
            return False
        print(f"[HID ATTEMPT] key={label}")
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, check=False, timeout=5,
            )
            if result.returncode == 0:
                print(f"[HID SENT] key={label}")
                return True
            print(f"[HID ERROR] key={label}; {result.stderr.strip() or 'System Events rejected keystroke'}")
        except Exception as exc:
            print(f"[HID ERROR] key={label}; {type(exc).__name__}: {exc}")
        return False

    def send_p(self):
        return self.send_key("p")

    def send_l(self):
        return self.send_key("l")

    def option_tab(self):
        # Keep the existing app-switch behavior without pynput.Controller.
        try:
            result = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to key code 48 using {command down}'],
                capture_output=True, text=True, check=False, timeout=5,
            )
            if result.returncode != 0:
                print(f"[HID ERROR] Cmd+Tab; {result.stderr.strip()}")
        except Exception as exc:
            print(f"[HID ERROR] Cmd+Tab; {type(exc).__name__}: {exc}")

    def _refresh_g2m_repeat_timeout(self):
        self.g2m_repeat_active = True
        self.g2m_repeat_until = time.monotonic() + GUITAR_REPEAT_TIMEOUT_SECONDS
        self.g2m_repeat_generation += 1
        token = self.g2m_repeat_generation
        try:
            threading.Thread(target=self._g2m_repeat_timeout_worker, args=(token,), daemon=True).start()
        except Exception as exc:
            self._log_runtime_exception("starting G2M repeat timeout", exc)
            self.g2m_repeat_active = False
            self.g2m_repeat_until = 0.0
            self.scenario_busy = False
            self.awaiting_tt = True
            print("[PROTECTION] G2M timeout worker could not start; returned to idle state.")

    def _g2m_repeat_timeout_worker(self, token):
        try:
            if self.stop_event.wait(GUITAR_REPEAT_TIMEOUT_SECONDS):
                return
            with self.lock:
                if self.g2m_repeat_active and self.g2m_repeat_generation == token:
                    self.g2m_repeat_active = False
                    self.g2m_repeat_until = 0.0
                    self.scenario_busy = False
                    self.awaiting_tt = True
                    print("[G2M] 2-second repeat window expired; returned to initial listening state")
        except Exception as exc:
            self._log_runtime_exception("G2M repeat timeout", exc)

    def guitar_swap_first_and_start_repeat_window(self, value):
        print(f"[G2M] first CH16 CC42 value={value} during TT1; forwarding CC42 through IAC before Cmd+Tab")
        self.send_cc(GUITAR_CHANNEL, GUITAR_CC, value)
        self.option_tab()
        with self.lock:
            self._refresh_g2m_repeat_timeout()
        print("[G2M] first command complete; waiting up to 2 seconds for another CC42")

    def guitar_repeat_cmd_tab_only(self, value):
        print(f"[G2M] repeated CH16 CC42 value={value}; forwarding CC42 and sending Cmd+Tab")
        self.send_cc(GUITAR_CHANNEL, GUITAR_CC, value)
        self.option_tab()
        self._refresh_g2m_repeat_timeout()

    def _refresh_tb_repeat_timeout(self):
        self.tb_repeat_active = True
        self.tb_repeat_until = time.monotonic() + GUITAR_REPEAT_TIMEOUT_SECONDS
        self.tb_repeat_generation += 1
        token = self.tb_repeat_generation
        try:
            threading.Thread(target=self._tb_repeat_timeout_worker, args=(token,), daemon=True).start()
        except Exception as exc:
            self._log_runtime_exception("starting TB repeat timeout", exc)
            self.tb_repeat_active = False
            self.tb_repeat_until = 0.0
            self.scenario_busy = False
            self.awaiting_tt = True
            print("[PROTECTION] TB timeout worker could not start; returned to idle state.")

    def _tb_repeat_timeout_worker(self, token):
        try:
            if self.stop_event.wait(GUITAR_REPEAT_TIMEOUT_SECONDS):
                return
            with self.lock:
                if self.tb_repeat_active and self.tb_repeat_generation == token:
                    self.tb_repeat_active = False
                    self.tb_repeat_until = 0.0
                    self.scenario_busy = False
                    self.awaiting_tt = True
                    print("[TB] 2-second repeat window expired; returned to initial listening state")
        except Exception as exc:
            self._log_runtime_exception("TB repeat timeout", exc)

    def tb_swap_first_and_start_repeat_window(self, value):
        print(f"[TB] first CH16 CC45 value={value} during TT1; forwarding TalkBack and toggling JamZone/SBP layout")
        self.send_cc(TB_CHANNEL, TB_CC, value)
        threading.Thread(target=self.toggle_jamzone_sbp_layout, name="AIRSTEPTBLayoutToggle", daemon=True).start()
        with self.lock:
            self._refresh_tb_repeat_timeout()
        print("[TB] first command complete; waiting up to 2 seconds for another CC45")

    def tb_repeat_talkback_and_toggle_layout(self, value):
        print(f"[TB] repeated CH16 CC45 value={value}; forwarding TalkBack and toggling JamZone/SBP layout")
        self.send_cc(TB_CHANNEL, TB_CC, value)
        threading.Thread(target=self.toggle_jamzone_sbp_layout, name="AIRSTEPTBLayoutToggle", daemon=True).start()
        self._refresh_tb_repeat_timeout()

    def activate_jamzone(self):
        # Never launch JamZone automatically: only activate it if already running.
        check = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to (name of processes) contains "Jamzone"'],
            capture_output=True, text=True, check=False, timeout=5,
        )
        if check.returncode != 0:
            print(f"[JAMZONE CHECK] Could not verify whether JamZone is running: {check.stderr.strip()}")
            return False
        if check.stdout.strip().lower() != "true":
            print("[JAMZONE CHECK] JamZone is not running; skipping activation and HID commands.")
            return False
        result = subprocess.run(
            ["osascript", "-e", 'tell application "Jamzone" to activate'],
            capture_output=True, text=True, check=False, timeout=5,
        )
        if result.returncode != 0:
            print("Could not activate already-running JamZone:", result.stderr.strip())
            return False
        print("[JAMZONE CHECK] JamZone was already running; activated without launching a new instance.")
        return True

    def _clear_fade(self):
        self.generation += 1
        self.fade = FadeState(generation=self.generation)

    def start_fade(self):
        with self.lock:
            now = time.monotonic()
            if now < self.lockout_until:
                return
            if self.fade.active:
                self.cancel_fade(start_lockout=False)
                self.awaiting_tt = True
                self.scenario_busy = False
                return
            # A fresh TT starts a new scenario; SBP22 is disabled for TT1.
            self.last_tt1_full_fade = False
            self.awaiting_tt = False
            self.scenario_busy = True
            # Always establish the known 0 dB starting point, avoiding a jump
            # from an unknown/old level to an unrelated value.
            try:
                self.send_volume(FULL_FADE_START_VALUE)
            except Exception as exc:
                self._log_runtime_exception("starting fade: initial volume command", exc)
                self.scenario_busy = False
                self.awaiting_tt = True
                self._clear_fade()
                print("[PROTECTION] Fade did not start; controller returned to idle state.")
                return
            self.generation += 1
            token = self.generation
            self.fade = FadeState(
                active=True,
                started_at=now,
                special_window_until=now + SPECIAL_WINDOW_SECONDS,
                current_value=FULL_FADE_START_VALUE,
                generation=token,
            )
            self.silent = False
        try:
            threading.Thread(target=self._full_fade_worker, args=(token,), daemon=True).start()
        except Exception as exc:
            self._log_runtime_exception("starting full fade worker", exc)
            with self.lock:
                if self.fade.active and self.fade.generation == token:
                    self._clear_fade()
                    self.scenario_busy = False
                    self.awaiting_tt = True
            print("[PROTECTION] Fade worker could not start; controller returned to idle state.")

    def _full_fade_worker(self, token):
        """Run the full-fade worker with controlled exception cleanup."""
        try:
            self._full_fade_worker_body(token)
        except Exception as exc:
            self._log_runtime_exception("full fade worker", exc)
            with self.lock:
                # Only clean up the scenario this worker belongs to.
                if self.fade.active and self.fade.generation == token:
                    self._clear_fade()
                    self.scenario_busy = False
                    self.awaiting_tt = True
                elif not self.fade.active and self.generation == token + 1 and self.scenario_busy:
                    # The worker may fail after it already cleared its FadeState,
                    # e.g. while activating JamZone or restoring the volume.
                    self.scenario_busy = False
                    self.awaiting_tt = True
            print("[PROTECTION] Full-fade worker failed; controller returned to idle state.")

    def _full_fade_worker_body(self, token):
        if self.stop_event.wait(PRE_FADE_DELAY_SECONDS):
            return
        with self.lock:
            if not self.fade.active or self.fade.generation != token:
                return
            if self.fade.short_action_active or self.fade.down_sequence_active:
                return
            self.fade.fade_started_at = time.monotonic()

        # 128 evenly spaced absolute values (127..0) over 15 seconds.
        steps = FULL_FADE_START_VALUE
        interval = FULL_FADE_SECONDS / steps
        start = time.monotonic()
        for i in range(1, steps + 1):
            if self.stop_event.is_set():
                return
            with self.lock:
                if not self.fade.active or self.fade.generation != token:
                    return
                if self.fade.short_action_active or self.fade.down_sequence_active:
                    return
                value = FULL_FADE_START_VALUE - i
                self.send_volume(value)
            deadline = start + i * interval
            self.stop_event.wait(max(0.0, deadline - time.monotonic()))

        with self.lock:
            if self.fade.active and self.fade.generation == token:
                self.send_volume(0)
                self._clear_fade()
                self.silent = True
            else:
                return

        # Explicitly activate JamZone instead of cycling apps with Option+Tab.
        # Then send P, wait 0.5s, and restore MainStage to 0 dB.
        if self.activate_jamzone_and_wait():
            self.send_p()
        time.sleep(0.5)
        self.send_volume(FULL_FADE_START_VALUE)
        self.silent = False
        with self.lock:
            self.last_tt1_full_fade = True
            self.scenario_busy = False
            self.awaiting_tt = True
        print("[TT1] Full fade-out completed; SBP22 remains disabled until a new TT1 scenario ends without full fade-out.")

    def cancel_fade(self, start_lockout=False):
        with self.lock:
            if not self.fade.active:
                if start_lockout:
                    self.lockout_until = time.monotonic() + LOCKOUT_SECONDS
                return
            now = time.monotonic()
            fade_started = self.fade.fade_started_at
            current_value = self.fade.current_value
            elapsed = max(0.0, now - fade_started) if fade_started else 0.0
            was_short_action = self.fade.short_action_active or self.fade.down_sequence_active
            self._clear_fade()
            restore_generation = self.generation
            if start_lockout:
                self.lockout_until = now + LOCKOUT_SECONDS
            self.scenario_busy = False
            self.awaiting_tt = True

        # For a full fade, restore only if the music was not already at silence.
        # The current CC value is the volume reached when cancellation arrived.
        if not was_short_action and fade_started and current_value > 0 and elapsed > 0:
            duration = max(0.05, elapsed / 3.0)
            try:
                threading.Thread(
                    target=self._restore_worker,
                    args=(current_value, FULL_FADE_START_VALUE, duration, restore_generation),
                    daemon=True
                ).start()
            except Exception as exc:
                self._log_runtime_exception("starting volume restore worker", exc)
                print("[PROTECTION] Volume restoration worker could not start; controller remains available for a new TT.")

    def _restore_worker(self, start_value, target_value, duration, generation_token=None):
        try:
            self._restore_worker_body(start_value, target_value, duration, generation_token)
        except Exception as exc:
            self._log_runtime_exception("volume restore worker", exc)
            print("[PROTECTION] Volume restore failed; scenario remains available for a new TT.")

    def _restore_worker_body(self, start_value, target_value, duration, generation_token=None):
        steps = abs(target_value - start_value)
        if steps == 0:
            return
        interval = duration / steps
        start = time.monotonic()
        direction = 1 if target_value > start_value else -1
        for i in range(1, steps + 1):
            if self.stop_event.is_set():
                return
            # A newer TT/scenario invalidates this restoration; never let an old
            # restore worker overwrite volume commands from a newer scenario.
            if generation_token is not None:
                with self.lock:
                    if self.generation != generation_token or self.fade.active or self.scenario_busy:
                        return
            self.send_volume(start_value + direction * i)
            deadline = start + i * interval
            self.stop_event.wait(max(0.0, deadline - time.monotonic()))

    def _send_effect_cc_after_delay(self, deadline):
        try:
            self._send_effect_cc_after_delay_body(deadline)
        except Exception as exc:
            self._log_runtime_exception("delayed sidechain worker", exc)

    def _send_effect_cc_after_delay_body(self, deadline):
        self.stop_event.wait(max(0.0, deadline - time.monotonic()))
        if self.stop_event.is_set():
            return
        # Always send at 7 seconds, even though the 1-second fade scenario
        # normally finishes first and clears FadeState/generation.
        self.send_effect_cc("7 seconds after quick-fade start")

    def short_fade_then_p(self):
        with self.lock:
            if not self.fade.active:
                threading.Thread(target=self.stop_playback_in_jamzone, daemon=True).start()
                return
            self.fade.short_action_active = True
            self.fade.quick_started_at = time.monotonic()
            self.fade.restore_master_after_30s = self.fade.fade_started_at > 0 and time.monotonic() <= self.fade.fade_started_at + FULL_FADE_SECONDS
            token = self.fade.generation
        try:
            threading.Thread(target=self._short_fade_worker_p, args=(token,), daemon=True).start()
        except Exception as exc:
            self._log_runtime_exception("starting quick fade worker (P)", exc)
            with self.lock:
                if self.fade.active and self.fade.generation == token:
                    self._clear_fade()
                self.scenario_busy = False
                self.awaiting_tt = True
            print("[PROTECTION] Quick fade worker (P) could not start; controller returned to idle state.")

    def _short_fade_worker_p(self, token):
        """Run this worker with exception logging and scenario cleanup."""
        try:
            self._short_fade_worker_p_body(token)
        except Exception as exc:
            self._log_runtime_exception("quick fade worker (P)", exc)
            with self.lock:
                if self.fade.active and self.fade.generation == token:
                    self._clear_fade()
                    self.scenario_busy = False
                    self.awaiting_tt = True
                elif not self.fade.active and self.generation == token + 1 and self.scenario_busy:
                    # The worker may fail after it already cleared its FadeState,
                    # e.g. while activating JamZone or restoring the volume.
                    self.scenario_busy = False
                    self.awaiting_tt = True
            print("[PROTECTION] quick fade worker (P) failed; controller returned to idle state.")

    def _short_fade_worker_p_body(self, token):
        with self.lock:
            restore_master_later = bool(self.fade.generation == token and self.fade.restore_master_after_30s)
        # Bypass the sidechain before the 1-second quick fade; restore normal
        # sidechain behavior by sending the same CC after 7 seconds.
        scenario_start = time.monotonic()
        self.send_effect_cc("quick-fade start, before fade begins")
        threading.Thread(
            target=self._send_effect_cc_after_delay,
            args=(scenario_start + 7.0,),
            daemon=True,
        ).start()
        start = scenario_start
        for i in range(1, SHORT_FADE_STEPS + 1):
            if self.stop_event.is_set():
                return
            with self.lock:
                if not self.fade.active or self.fade.generation != token:
                    return
                self.send_quick_volume(127 - i)
            self.stop_event.wait(max(
                0.0,
                start + i * SHORT_FADE_SECONDS / SHORT_FADE_STEPS - time.monotonic()
            ))

        with self.lock:
            if not self.fade.active or self.fade.generation != token:
                return
            self._clear_fade()
            self.silent = True

        if self.activate_jamzone_and_wait():
            self.send_p()
        self.stop_event.wait(0.5)
        self.send_quick_volume(127)
        if restore_master_later:
            print('[QUICK FADE] Triggered during the 15-second fade window; restoring master volume to 0 dB in 30 seconds.')
            self.stop_event.wait(30.0)
            if self.stop_event.is_set():
                return
            self.send_volume(127)
        self.silent = False
        with self.lock:
            self.scenario_busy = False
            self.awaiting_tt = True

    def down_sequence(self):
        with self.lock:
            if not self.fade.active or self.fade.down_sequence_active: return
            self.fade.down_sequence_active = True
            self.fade.quick_started_at = time.monotonic()
            self.fade.restore_master_after_30s = self.fade.fade_started_at > 0 and time.monotonic() <= self.fade.fade_started_at + FULL_FADE_SECONDS
            token = self.fade.generation
        try:
            threading.Thread(target=self._down_worker, args=(token,), daemon=True).start()
        except Exception as exc:
            self._log_runtime_exception("starting quick fade worker (Down)", exc)
            with self.lock:
                if self.fade.active and self.fade.generation == token:
                    self._clear_fade()
                self.scenario_busy = False
                self.awaiting_tt = True
            print("[PROTECTION] Quick fade worker (Down) could not start; controller returned to idle state.")

    def _down_worker(self, token):
        """Run this worker with exception logging and scenario cleanup."""
        try:
            self._down_worker_body(token)
        except Exception as exc:
            self._log_runtime_exception("quick fade worker (Down)", exc)
            with self.lock:
                if self.fade.active and self.fade.generation == token:
                    self._clear_fade()
                    self.scenario_busy = False
                    self.awaiting_tt = True
                elif not self.fade.active and self.generation == token + 1 and self.scenario_busy:
                    # The worker may fail after it already cleared its FadeState,
                    # e.g. while activating JamZone or restoring the volume.
                    self.scenario_busy = False
                    self.awaiting_tt = True
            print("[PROTECTION] quick fade worker (Down) failed; controller returned to idle state.")

    def _down_worker_body(self, token):
        with self.lock:
            restore_master_later = bool(self.fade.generation == token and self.fade.restore_master_after_30s)
        # Bypass the sidechain before the 1-second quick fade; restore normal
        # sidechain behavior by sending the same CC after 7 seconds.
        scenario_start = time.monotonic()
        self.send_effect_cc("quick-fade start, before fade begins")
        threading.Thread(
            target=self._send_effect_cc_after_delay,
            args=(scenario_start + 7.0,),
            daemon=True,
        ).start()

        # Activate JamZone concurrently with the 1-second fade, so activation
        # time does not get added to the original 1.5-second P schedule.
        started = time.monotonic()
        activation_done = threading.Event()
        activation_ok = {"ok": False, "finished_at": 0.0}

        def activate():
            try:
                activation_ok["ok"] = self.activate_jamzone()
            except Exception as exc:
                self._log_runtime_exception("JamZone activation thread", exc)
                activation_ok["ok"] = False
            finally:
                activation_ok["finished_at"] = time.monotonic()
                activation_done.set()

        threading.Thread(target=activate, daemon=True).start()

        for i in range(1, SHORT_FADE_STEPS + 1):
            if self.stop_event.is_set():
                return
            with self.lock:
                if not self.fade.active or self.fade.generation != token:
                    return
                self.send_quick_volume(127 - i)
            self.stop_event.wait(max(
                0.0,
                started + i * SHORT_FADE_SECONDS / SHORT_FADE_STEPS - time.monotonic()
            ))

        # P is scheduled for 1.5s after Down, but never before JamZone has
        # become frontmost and had the required 0.5s to accept HID.
        target_p_time = started + DOWN_TO_P_SECONDS
        self.stop_event.wait(max(0.0, target_p_time - time.monotonic()))
        if not activation_done.is_set():
            activation_done.wait()
        if not activation_ok["ok"]:
            if not self.activate_jamzone_and_wait():
                # Do not leave the controller permanently busy if JamZone
                # cannot be activated by either attempt.
                with self.lock:
                    if self.fade.active and self.fade.generation == token:
                        self._clear_fade()
                    self.scenario_busy = False
                    self.awaiting_tt = True
                print("[PROTECTION] JamZone activation failed twice; controller returned to idle state.")
                return
        else:
            # Ensure at least 0.5s elapsed since activation completed.
            # Usually already satisfied by the 1-second fade + 0.5s wait.
            self.stop_event.wait(max(
                0.0, 0.5 - (time.monotonic() - activation_ok["finished_at"])
            ))

        with self.lock:
            if not self.fade.active or self.fade.generation != token:
                return
        # P at 1.5s after Down; JamZone has already been frontmost for >=0.5s.
        self.send_p()

        # L half a second after P.
        self.stop_event.wait(P_TO_L_SECONDS)
        with self.lock:
            if not self.fade.active or self.fade.generation != token:
                return
        self.send_l()

        # The physical/HID trigger also sent Down to JamZone; send Up 0.5s
        # after L to release that key and avoid leaving it held.
        self.stop_event.wait(0.5)
        with self.lock:
            if not self.fade.active or self.fade.generation != token:
                return
        self.send_key(keyboard.Key.up)

        with self.lock:
            if self.fade.active and self.fade.generation == token:
                self._clear_fade()
                self.silent = True

        # As in the other quick fade, restore the target channel to 0 dB
        # half a second after the final HID Up command.
        self.stop_event.wait(0.5)
        self.send_quick_volume(127)
        if restore_master_later:
            print('[LOOP FADE] Triggered during the 15-second fade window; restoring master volume to 0 dB in 30 seconds.')
            self.stop_event.wait(30.0)
            if self.stop_event.is_set():
                return
            self.send_volume(127)
        self.silent = False
        with self.lock:
            self.scenario_busy = False
            self.awaiting_tt = True

    def handle_midi(self, msg):
        # CC11 is a high-rate expression stream; logging every value floods the
        # console and adds avoidable I/O. All MIDI processing remains unchanged.
        if VERBOSE_MIDI_LOGGING and not (msg.type == 'control_change' and msg.control == 11):
            print(f"[MIDI RECEIVED <- AIRSTEP] {format_midi_message(msg)}")
        now = time.monotonic()
        with self.lock:
            is_tt = (
                msg.type == "control_change"
                and msg.channel == TT_CHANNEL
                and msg.control == TT_CC
            )
            is_guitar = (
                msg.type == "control_change"
                and msg.channel == GUITAR_CHANNEL
                and msg.control == GUITAR_CC
            )
            # After a second TT cancels a scenario, ignore all commands for
            # 2.5 seconds. Each TT passes through unchanged and restarts the timer.
            if now < self.lockout_until:
                if is_tt:
                    self.send_iac_message(msg, "TT forwarded during lockout")
                    self.lockout_until = now + LOCKOUT_SECONDS
                return
            elif self.lockout_until:
                # Lockout expired: return to the initial waiting state.
                self.lockout_until = 0.0
                self._clear_fade()
                self.scenario_busy = False
                self.awaiting_tt = True

            is_tb = (
                msg.type == "control_change"
                and msg.channel == TB_CHANNEL
                and msg.control == TB_CC
            )
            # During either 2-second repeat window, only that scenario's button
            # repeats its paired actions; a fresh TT starts a new TT1.
            if self.g2m_repeat_active or self.tb_repeat_active:
                if is_tt:
                    was_tb_repeat = self.tb_repeat_active
                    self.g2m_repeat_active = False
                    self.g2m_repeat_until = 0.0
                    self.g2m_repeat_generation += 1
                    self.tb_repeat_active = False
                    self.tb_repeat_until = 0.0
                    self.tb_repeat_generation += 1
                    if was_tb_repeat:
                        self.scenario_busy = False
                        self.awaiting_tt = True
                elif self.g2m_repeat_active and is_guitar:
                    self.guitar_repeat_cmd_tab_only(msg.value)
                    return
                elif self.tb_repeat_active and is_tb:
                    self.tb_repeat_talkback_and_toggle_layout(msg.value)
                    return
                else:
                    return

            # After a scenario ends, ignore everything until a fresh TT, except
            # SBP22: it is restored only when the last TT1 did not complete full fade-out.
            if self.awaiting_tt and not is_tt:
                is_cc22 = (
                    msg.type == "control_change"
                    and msg.channel == PLAY_CHANNEL
                    and msg.control == PLAY_CC
                )
                if is_cc22 and self.last_tt1_full_fade and msg.value == PLAY_VALUE:
                    # Preserve v52 behavior after a completed full fade: send P only.
                    # Do not route through SBP22 and do not change the front application.
                    print("[CC22] Full fade completed; restoring v52 behavior: send P only, without SBP activation.")
                    threading.Thread(target=self.send_p, name="CC22PostFullFadeP", daemon=True).start()
                elif is_cc22 and not self.last_tt1_full_fade and self.layout_compact:
                    # SBP22 exists only in split/compact JamZone + SongBookPro layout.
                    print("[SBP22] Split layout: activate JamZone, send P, return to SongBookPro")
                    threading.Thread(
                        target=self._compact_cc22_action,
                        name="CompactCC22Action",
                        daemon=True,
                    ).start()
                elif is_cc22 and not self.last_tt1_full_fade and not self.layout_compact:
                    # CC22 in full-width JamZone-only layout never activates SBP.
                    print("[CC22] JamZone-only layout: activate JamZone and send P; SBP22 inactive.")
                    threading.Thread(
                        target=self._jamzone_only_cc22_action,
                        name="JamZoneOnlyCC22Action",
                        daemon=True,
                    ).start()
                return

            # Non-CC MIDI is never passed through during an active scenario.
            if msg.type != "control_change":
                if not self.scenario_busy:
                    return
                self.send_iac_message(msg)
                return

            ch, cc, value = msg.channel, msg.control, msg.value
            active = self.fade.active
            in_special = active and now <= self.fade.special_window_until

            if is_tt:
                if active:
                    # A second TT cancels the active scenario and starts a
                    # 2.5-second lockout; the original TT message is forwarded.
                    self.cancel_fade(start_lockout=True)
                    self.send_iac_message(msg, "TT forwarded; lockout restarted")
                elif self.awaiting_tt:
                    self.start_fade()
                return

            if ch == PLAY_CHANNEL and cc == PLAY_CC and value == PLAY_VALUE:
                if active and self.scenario_busy:
                    self.short_fade_then_p()
                return

            if is_guitar:
                # G2M is valid only during TT1's initial 5-second window.
                if active and self.scenario_busy and in_special:
                    self.cancel_fade(start_lockout=False)
                    with self.lock:
                        self._clear_fade()
                        self.guitar_only_until = 0.0
                        self.scenario_busy = True
                        self.awaiting_tt = False
                    self.guitar_swap_first_and_start_repeat_window(value)
                return

            if is_tb:
                # AIRSTEP TalkBack gets the same initial TT1 window and 2-second
                # repeat scenario as G2M/Cmd+Tab, but repeats TalkBack + Note 94 action.
                if active and self.scenario_busy and in_special:
                    self.cancel_fade(start_lockout=False)
                    with self.lock:
                        self._clear_fade()
                        self.guitar_only_until = 0.0
                        self.scenario_busy = True
                        self.awaiting_tt = False
                    self.tb_swap_first_and_start_repeat_window(value)
                return

            # Unknown MIDI is ignored during the active TT scenario.
            return

    def _log_runtime_exception(self, context, exc):
        # Runtime containment must not itself interrupt MIDI control.
        self.fault_detected_at = time.monotonic()
        print(f'[PROTECTION ERROR] {context}: {type(exc).__name__}: {exc}')
        _write_crash_record(
            f'RUNTIME EXCEPTION ({context})',
            type(exc), exc, exc.__traceback__,
        )

    def _safe_on_key(self, key):
        # Keep a callback exception from unexpectedly terminating keyboard handling.
        try:
            self.on_key(key)
        except Exception as exc:
            self._log_runtime_exception('keyboard callback', exc)

    def _safe_on_key_release(self, key):
        try:
            self.on_key_release(key)
        except Exception as exc:
            self._log_runtime_exception('keyboard release callback', exc)

    @staticmethod
    def _is_modifier_key(key, modifier):
        variants = {
            'ctrl': (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r),
            'alt': (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r),
            'shift': (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r),
        }
        return key in variants[modifier]

    def on_key(self, key):
        # Track modifiers without suppressing or consuming keyboard input.
        if self._is_modifier_key(key, 'ctrl'):
            self._pressed_keys.add('ctrl')
        elif self._is_modifier_key(key, 'alt'):
            self._pressed_keys.add('alt')
        elif self._is_modifier_key(key, 'shift'):
            self._pressed_keys.add('shift')

        if key == keyboard.Key.down:
            with self.lock:
                if self.scenario_busy and self.fade.active:
                    self.down_sequence()
            return

        # Dedicated immediate full-process reset: physical § key alone (no modifiers).
        # No confirmation dialog; the Tk main thread performs the restart sequence.
        # Do not access key.char here: on macOS it can call HIToolbox input-source
        # APIs from the pynput worker thread (the supplied crash report's failure path).
        # Use the physical macOS virtual key code for the section-sign key on ISO keyboards.
        key_vk = getattr(key, 'vk', None)
        if (key_vk == 10 and not self._restart_shortcut_latched and not self._pressed_keys):
            self._restart_shortcut_latched = True
            self.restart_shortcut_requested = True

    def on_key_release(self, key):
        if getattr(key, 'vk', None) == 10:
            self._restart_shortcut_latched = False
        if self._is_modifier_key(key, 'ctrl'):
            self._pressed_keys.discard('ctrl')
        elif self._is_modifier_key(key, 'alt'):
            self._pressed_keys.discard('alt')
        elif self._is_modifier_key(key, 'shift'):
            self._pressed_keys.discard('shift')

    def _handle_midi_input_callback(self, msg):
        """Process AIRSTEP messages delivered by the backend callback thread."""
        if self.stop_event.is_set():
            return
        try:
            with self.lock:
                self._record_midi_in(msg)
            self.handle_midi(msg)
            # Update discrete commands promptly; continuous CC11 expression traffic is
            # deliberately excluded to avoid extra UI work during high-rate streams.
            if not (msg.type == 'control_change' and getattr(msg, 'control', None) == 11):
                bar = getattr(self, 'floating_bar', None)
                if bar is not None:
                    bar.request_ui_refresh()
        except Exception as exc:
            self._log_runtime_exception('MIDI event handler', exc)

    def _midi_receive_loop(self):
        """Supervise AIRSTEP's CoreMIDI endpoint and reopen it after temporary loss.

        This loop deliberately does not restart the controller process. It checks the
        endpoint once per second, closes stale input handles while the endpoint is
        absent, and opens a fresh callback-based input when the endpoint returns.
        """
        last_open_error = None
        last_error_log_at = 0.0
        while not self.stop_event.is_set():
            with self.port_status_lock:
                snapshot_inputs, _snapshot_outputs, snapshot_error = self.port_status_snapshot
            if snapshot_error or snapshot_inputs is None:
                now = time.monotonic()
                error_key = f'CoreMIDI enumeration unavailable: {snapshot_error or "no snapshot"}'
                if self.in_port is not None:
                    try:
                        self.in_port.close()
                    except Exception:
                        pass
                    self.in_port = None
                if error_key != last_open_error or now - last_error_log_at >= 10.0:
                    print(f'[BT MIDI RECOVERY] {error_key}; closed any stale AIRSTEP input and will retry.')
                    last_open_error = error_key
                    last_error_log_at = now
                self.stop_event.wait(1.0)
                continue
            available_inputs = snapshot_inputs

            if AIRSTEP_NAME not in available_inputs:
                if self.in_port is not None:
                    try:
                        self.in_port.close()
                    except Exception as exc:
                        print(f'[BT MIDI RECOVERY] Closing stale AIRSTEP input: {type(exc).__name__}: {exc}')
                    self.in_port = None
                    print('[BT MIDI RECOVERY] AIRSTEP is absent from CoreMIDI; stale input closed. Waiting to reopen.')
                self.receiver_failed = False
                last_open_error = None
                self.stop_event.wait(1.0)
                continue

            if self.in_port is None:
                try:
                    new_port = mido.open_input(AIRSTEP_NAME, callback=self._handle_midi_input_callback)
                    self.in_port = new_port
                    self.receiver_failed = False
                    if last_open_error is None:
                        print(f'[BT MIDI RECOVERY] Opened MIDI input: {AIRSTEP_NAME}')
                    else:
                        print(f'[BT MIDI RECOVERY] Reopened MIDI input after error: {AIRSTEP_NAME}')
                    last_open_error = None
                except Exception as exc:
                    now = time.monotonic()
                    error_key = f'{type(exc).__name__}: {exc}'
                    if error_key != last_open_error or now - last_error_log_at >= 10.0:
                        print(f'[BT MIDI RECOVERY] Could not open {AIRSTEP_NAME}: {error_key}; retrying.')
                        last_open_error = error_key
                        last_error_log_at = now
                    self.in_port = None

            self.stop_event.wait(1.0)

    def run(self):
        inputs, outputs = mido.get_input_names(), mido.get_output_names()
        # AIRSTEP input is supervised in its own worker, including when absent at startup.
        # Temporary Bluetooth/CoreMIDI loss never restarts the controller process.
        self.in_port = None
        if AIRSTEP_NAME in inputs:
            print(f'[MIDI STATUS] {AIRSTEP_NAME} is present; receiver will open it in the recovery worker.')
        else:
            print(f'[MIDI STATUS] WARNING: Input {AIRSTEP_NAME!r} not found at startup. The recovery worker will keep checking and open it when it returns.')
        if IAC_NAME not in outputs:
            raise RuntimeError(f"Output '{IAC_NAME}' not found. Available: {outputs}")
        self.out_port = mido.open_output(IAC_NAME)
        # Diagnostic-only listener: inspect MainStage's state stream on Bus 2.
        iac2_candidates = [name for name in inputs if IAC_STATE_INPUT_NAME in name]
        if iac2_candidates:
            try:
                self.iac_state_in_port = mido.open_input(iac2_candidates[0])
                print(f'MIDI diagnostic input: {iac2_candidates[0]}')
            except Exception as exc:
                print(f'[IAC BUS2 WARNING] Could not open Bus 2: {type(exc).__name__}: {exc}')
        else:
            print(f'[IAC BUS2 WARNING] Input {IAC_STATE_INPUT_NAME!r} not found; available inputs: {inputs}')
        # SMC input is opened by the ongoing recovery supervisor, including when
        # Bluetooth connects after this process has already started.
        smc_candidates = [name for name in inputs if 'SMC-Mixer Bluetooth' in name]
        if not smc_candidates:
            print('[SMC STATE] SMC-Mixer Bluetooth not present at startup; recovery will keep checking.')
        listener = RawMacKeyboardListener(self)
        listener.start()
        print('Running with integrated floating status bar.')
        print('[UI] Active tile colors are driven by SMC state-note input; watch [SMC STATE] logs when pressing mapped buttons.')
        print(f'MIDI input : {AIRSTEP_NAME} (automatic reconnect enabled)')
        print(f'MIDI output: {IAC_NAME}')
        print('Floating Bar listens for MainStage status mirrors on IAC Driver Bus 2, MIDI CH15. Ensure each button mapping sends its configured state CC/Note on activation and deactivation; no separate request command is sent.')
        print('Close/hide the bar with Escape or the window close control; this does not stop MIDI.')
        receiver = threading.Thread(target=self._midi_receive_loop, name='MIDIReceiveLoop', daemon=True)
        self.receiver_thread = receiver
        receiver.start()
        if self.iac_state_in_port is not None:
            self.iac_state_receiver_thread = threading.Thread(target=self._iac_bus2_receive_loop, name='IACBus2Diagnostic', daemon=True)
            self.iac_state_receiver_thread.start()
        # Initial enumeration happens during startup; ongoing enumeration is off the UI thread.
        self._log_bluetooth_midi_status(force=True, inputs=inputs)
        self.port_status_thread = threading.Thread(target=self._port_status_monitor_loop, name='MIDIPortStatusMonitor', daemon=True)
        self.port_status_thread.start()
        self.battery_scheduler_thread = threading.Thread(target=self._battery_scheduler_loop, name='ControllerBatteryScheduler', daemon=True)
        self.battery_scheduler_thread.start()
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, name='UIWatchdog', daemon=True)
        self._watchdog_thread.start()
        root = tk.Tk()
        # Cache macOS screen dimensions on the Tk main thread for layout commands.
        self.screen_width = root.winfo_screenwidth()
        self.screen_height = root.winfo_screenheight()
        self.root = root
        self.floating_bar = FloatingBar(root, self)
        if self._pending_bar_visibility_toggle:
            self._pending_bar_visibility_toggle = False
            root.after(0, self.floating_bar._toggle_bar_visibility)
        # Start syncing existing button commands immediately; no startup delay.
        root.after(0, lambda: self.startup_button_sync(root))
        try:
            root.mainloop()
        finally:
            self.stop_event.set()
            listener.stop()
            try:
                if self.in_port is not None:
                    self.in_port.close()
            finally:
                try:
                    if self.smc_in_port is not None:
                        self.smc_in_port.close()
                finally:
                    try:
                        if self.iac_state_in_port is not None:
                            self.iac_state_in_port.close()
                    finally:
                        if self.out_port is not None:
                            self.out_port.close()
        if self.restart_requested:
            _write_crash_record(
                f'FULL PROCESS RESTART: old resources closed; replacing process (source={self.restart_source}).'
            )
            print('[RESTART] Old controller resources closed; replacing process now.')
            os.environ['MAINSTAGE_JAMZONE_RESTART_PENDING'] = '1'
            os.execv(sys.executable, [sys.executable] + sys.argv)

if __name__ == "__main__":
    _install_crash_diagnostics()
    Controller().run()
