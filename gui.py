"""WindowPilot's main window. Several jobs can run at once — one per app window, each with its own tab."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from pynput import keyboard

import winapi
from agent import Agent
from capture import Capturer
from config import APP_NAME, MODELS, SPEED_PRESETS, Config, app_dir
from notifier import notify
from recorder import Recorder
from tasks import SavedTask, TaskLibrary, guide_from_recording, reference_frames
from version import RELEASE_DATE, __version__

STOP_HOTKEY = "<ctrl>+<shift>+<f12>"
STOP_HOTKEY_LABEL = "Ctrl+Shift+F12"
INPUT_MODE_LABELS = {"takeover": "Take over", "share": "Share", "background": "Background"}
INPUT_MODE_HINTS = {
    "takeover": "Take over: the AI uses your mouse and keyboard. Most reliable, but you can't work meanwhile.",
    "share": "Share: the AI waits until you pause ~1s, acts in a blink, then gives your cursor and window back. "
             "Works with every app.",
    "background": "Background: the AI gets its own invisible mouse and keyboard — keep working normally. Works "
                  "with many classic apps, but not most browsers, games or modern apps (then use Share).",
}
DEMO_PROMPT = "Repeat the workflow I just demonstrated until everything is done, then tell me what you did."
STATE_ICONS = {"idle": "•", "running": "▶", "DONE": "✅", "NEEDS_INPUT": "❓", "FAILED": "❌", "STOPPED": "⏹"}
RESULT_LABELS = {"DONE": "✅ Done", "NEEDS_INPUT": "❓ Needs your input", "FAILED": "❌ Couldn't finish",
                 "STOPPED": "⏹ Stopped"}


def make_transcript(parent) -> scrolledtext.ScrolledText:
    t = scrolledtext.ScrolledText(parent, wrap="word", state="disabled", height=14, font=("Segoe UI", 10),
                                  relief="flat", padx=8, pady=6)
    t.tag_configure("you", foreground="#1d4ed8", font=("Segoe UI", 10, "bold"), spacing1=8)
    t.tag_configure("claude", foreground="#111827", spacing1=4)
    t.tag_configure("action", foreground="#6b7280", font=("Consolas", 9), lmargin1=18, lmargin2=18)
    t.tag_configure("status", foreground="#047857", font=("Segoe UI", 9, "italic"), spacing1=4)
    t.tag_configure("error", foreground="#b91c1c", spacing1=4)
    t.tag_configure("result", foreground="#111827", background="#ecfdf5", spacing1=6, spacing3=6,
                    lmargin1=6, lmargin2=6)
    return t


def write_transcript(t: scrolledtext.ScrolledText, kind: str, text: str) -> None:
    prefix = {"you": "You:  ", "action": "→ ", "error": "⚠ "}.get(kind, "")
    t.configure(state="normal")
    t.insert("end", prefix + text.strip() + "\n", kind)
    t.configure(state="disabled")
    t.see("end")


class Job:
    """One AI assistant working in one window, with its own conversation and tab."""
    _next_id = 1

    def __init__(self, app: "App", target: winapi.WindowInfo) -> None:
        self.app, self.target = app, target
        self.id = Job._next_id
        Job._next_id += 1
        self.state = "idle"
        self.mode = "takeover"
        q = app.q
        self.agent = Agent(app.cfg, app.capturer,
                           log=lambda kind, text: q.put(("jlog", self.id, kind, text)),
                           status=lambda text: q.put(("jstatus", self.id, text)),
                           usage=lambda totals: q.put(("usage",)),
                           approve=lambda text: app.approve_from_worker(f"[{self.short}]\n{text}", self.agent),
                           timing=lambda text: q.put(("jtiming", self.id, text)))
        self._build_tab()

    @property
    def short(self) -> str:
        t = self.target.title
        return t if len(t) <= 26 else t[:24] + "…"

    @property
    def running(self) -> bool:
        return self.state == "running"

    def _build_tab(self) -> None:
        self.frame = ttk.Frame(self.app.notebook, padding=4)
        bar = ttk.Frame(self.frame)
        bar.pack(fill="x", pady=(0, 4))
        self.status_label = ttk.Label(bar, text="Ready", foreground="#374151")
        self.status_label.pack(side="left")
        self.btn_close = ttk.Button(bar, text="✕ Close", command=lambda: self.app.close_job(self))
        self.btn_close.pack(side="right", padx=2)
        self.btn_new = ttk.Button(bar, text="↺ New conversation", command=lambda: self.app.reset_job(self))
        self.btn_new.pack(side="right", padx=2)
        self.btn_stop = ttk.Button(bar, text="■ Stop", command=self.stop, state="disabled")
        self.btn_stop.pack(side="right", padx=2)
        self.timing_label = ttk.Label(bar, text="", foreground="#6b7280")
        self.timing_label.pack(side="right", padx=10)
        self.text = make_transcript(self.frame)
        self.text.pack(fill="both", expand=True)
        self.app.notebook.add(self.frame, text=self.tab_title())

    def tab_title(self) -> str:
        return f"{STATE_ICONS.get(self.state, '•')} {self.short}"

    def set_state(self, state: str) -> None:
        self.state = state
        self.app.notebook.tab(self.frame, text=self.tab_title())
        busy = state == "running"
        self.btn_stop.config(state="normal" if busy else "disabled")
        self.btn_new.config(state="disabled" if busy else "normal")
        self.btn_close.config(state="disabled" if busy else "normal")

    def stop(self) -> None:
        if self.running:
            self.agent.stop()
            self.status_label.config(text="Stopping…")
            self.log("status", "Stop requested — finishing the current action.")

    def log(self, kind: str, text: str) -> None:
        write_transcript(self.text, kind, text)
        self.app.session_log(self.short, kind, text)


class App:
    def __init__(self) -> None:
        self.cfg = Config.load()
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} v{__version__}")
        self.root.geometry("900x860")
        self.root.minsize(700, 640)
        try:
            self.root.tk.call("tk", "scaling", winapi.system_dpi() / 72.0)
        except tk.TclError:
            pass

        self.q: queue.Queue = queue.Queue()
        self.capturer = Capturer()
        self.recorder = Recorder(self.capturer, on_update=lambda e, f: self.q.put(("rec", e, f)))
        self.analyzer = Agent(self.cfg, self.capturer,
                              log=lambda kind, text: self.q.put(("log", kind, text)),
                              status=lambda text: self.q.put(("status", text)),
                              usage=lambda totals: self.q.put(("usage",)),
                              approve=lambda text: False)
        self.library = TaskLibrary()
        self.saved: list[SavedTask] = []
        self.jobs: dict[int, Job] = {}
        self.closed_totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.windows: list[winapi.WindowInfo] = []
        self.recording = None
        self.recording_active = False
        self.analyzing = False
        self.analysis: str | None = None
        self.last_prompt = ""
        self.last_target: winapi.WindowInfo | None = None
        self.overlay: tk.Toplevel | None = None
        self._overlay_corner = None
        self.hotkeys = None
        self.session_dir: Path | None = None
        self._multi_tip_shown = False

        self._build_ui()
        self.refresh_windows()
        self.refresh_saved()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll)
        self._log("status", "Welcome! Select one or more windows (Ctrl/Shift-click), then tell me what to do — or "
                            "press Record to show me. Each window gets its own job tab, and jobs run at the same "
                            f"time. Press {STOP_HOTKEY_LABEL} anytime to stop everything.")
        if self.cfg.last_version != __version__:
            if self.cfg.last_version:
                self._log("status", f"Updated from v{self.cfg.last_version} to v{__version__}. "
                                    "See Help → What's new.")
            self.cfg.last_version = __version__
            self.cfg.save()
        if not self.cfg.effective_api_key():
            self.root.after(400, self.open_settings)

    # ================================================================ UI construction

    def _build_ui(self) -> None:
        root = self.root
        menubar = tk.Menu(root)
        filem = tk.Menu(menubar, tearoff=False)
        filem.add_command(label="Settings…", command=self.open_settings)
        filem.add_command(label="Open data folder", command=lambda: os.startfile(app_dir()))
        filem.add_separator()
        filem.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=filem)
        helpm = tk.Menu(menubar, tearoff=False)
        helpm.add_command(label="How to use", command=self.show_help)
        helpm.add_command(label="What's new", command=self.show_whats_new)
        helpm.add_separator()
        helpm.add_command(label=f"About {APP_NAME}", command=self.show_about)
        menubar.add_cascade(label="Help", menu=helpm)
        root.config(menu=menubar)

        outer = ttk.Frame(root, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        # --- window list (multi-select)
        pick = ttk.LabelFrame(outer, text=" 1. Window(s) to work in — Ctrl/Shift-click to pick several ",
                              padding=8)
        pick.grid(row=0, column=0, sticky="ew")
        pick.columnconfigure(0, weight=1)
        lb_frame = ttk.Frame(pick)
        lb_frame.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        lb_frame.columnconfigure(0, weight=1)
        self.win_list = tk.Listbox(lb_frame, height=5, selectmode="extended", exportselection=False,
                                   font=("Segoe UI", 10), activestyle="none")
        self.win_list.grid(row=0, column=0, sticky="ew")
        sb = ttk.Scrollbar(lb_frame, orient="vertical", command=self.win_list.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.win_list.config(yscrollcommand=sb.set)
        self.win_list.bind("<<ListboxSelect>>", lambda e: self._on_window_selected())
        side = ttk.Frame(pick)
        side.grid(row=0, column=1, sticky="n")
        ttk.Button(side, text="⟳ Refresh", command=self.refresh_windows).pack(fill="x", pady=1)
        self.btn_pick = ttk.Button(side, text="🎯 Hover-pick", command=self.hover_pick)
        self.btn_pick.pack(fill="x", pady=1)
        ttk.Button(side, text="Show", command=self.show_target).pack(fill="x", pady=1)

        # --- tabs: General + one per job
        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=1, column=0, sticky="nsew", pady=8)
        gen = ttk.Frame(self.notebook, padding=4)
        self.general = make_transcript(gen)
        self.general.pack(fill="both", expand=True)
        self.notebook.add(gen, text="General")

        # --- saved tasks
        saved = ttk.LabelFrame(outer, text=" ★ Saved tasks ", padding=8)
        saved.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        saved.columnconfigure(0, weight=1)
        self.saved_var = tk.StringVar()
        self.saved_combo = ttk.Combobox(saved, textvariable=self.saved_var, state="readonly")
        self.saved_combo.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(saved, text="▶ Run saved task", command=self.on_run_saved).grid(row=0, column=1, padx=2)
        ttk.Button(saved, text="✎ Edit", command=self.on_edit_saved).grid(row=0, column=2, padx=2)
        ttk.Button(saved, text="🗑 Delete", command=self.on_delete_saved).grid(row=0, column=3, padx=2)
        ttk.Label(saved, text="Runs in every selected window (or finds the right app if none is selected). "
                              "Text in the task box below is added as extra instructions.",
                  foreground="#6b7280").grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # --- teach by demonstration
        teach = ttk.LabelFrame(outer, text=" 2. Teach by demonstration (optional) ", padding=8)
        teach.grid(row=3, column=0, sticky="ew")
        self.btn_record = ttk.Button(teach, text="● Record", command=self.on_record)
        self.btn_record.pack(side="left", padx=2)
        self.btn_stop_rec = ttk.Button(teach, text="■ Stop recording", command=self.on_stop_record,
                                       state="disabled")
        self.btn_stop_rec.pack(side="left", padx=2)
        self.btn_analyze = ttk.Button(teach, text="🔍 Analyze recording", command=self.on_analyze,
                                      state="disabled")
        self.btn_analyze.pack(side="left", padx=2)
        self.btn_save_task = ttk.Button(teach, text="💾 Save as task…", command=self.on_save_task,
                                        state="disabled")
        self.btn_save_task.pack(side="left", padx=2)
        self.rec_label = ttk.Label(teach, text="No recording yet", foreground="#6b7280")
        self.rec_label.pack(side="left", padx=10)

        # --- task prompt
        task = ttk.LabelFrame(outer, text=" 3. What should I do? (runs in every selected window) ", padding=8)
        task.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        task.columnconfigure(0, weight=1)
        self.prompt = tk.Text(task, height=4, wrap="word", font=("Segoe UI", 10), relief="solid", bd=1)
        self.prompt.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.prompt.bind("<Control-Return>", lambda e: (self.on_run(), "break")[1])
        opts = ttk.Frame(task)
        opts.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.use_ref_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Use my recording", variable=self.use_ref_var).pack(side="left")
        self.confirm_var = tk.BooleanVar(value=self.cfg.confirm_each_step)
        ttk.Checkbutton(opts, text="Ask before each action", variable=self.confirm_var).pack(side="left", padx=10)
        ttk.Label(opts, text="Speed:").pack(side="left")
        self.speed_var = tk.StringVar(value=SPEED_PRESETS[self.cfg.speed]["label"])
        speed = ttk.Combobox(opts, textvariable=self.speed_var, state="readonly", width=9,
                             values=[p["label"] for p in SPEED_PRESETS.values()])
        speed.pack(side="left", padx=(4, 0))
        speed.bind("<<ComboboxSelected>>", lambda e: self._on_speed())
        ttk.Label(opts, text="Input:").pack(side="left", padx=(10, 0))
        self.mode_var = tk.StringVar(value=INPUT_MODE_LABELS["takeover"])
        mode = ttk.Combobox(opts, textvariable=self.mode_var, state="readonly", width=11,
                            values=list(INPUT_MODE_LABELS.values()))
        mode.pack(side="left", padx=(4, 0))
        mode.bind("<<ComboboxSelected>>", lambda e: self._on_input_mode())
        self.btn_stop_all = ttk.Button(task, text="■ Stop all", command=self.stop_all, state="disabled")
        self.btn_stop_all.grid(row=1, column=1, padx=2, pady=(6, 0))
        self.btn_run = ttk.Button(task, text="▶ Run  (Ctrl+Enter)", command=self.on_run)
        self.btn_run.grid(row=1, column=2, padx=2, pady=(6, 0))

        # --- status bar
        bar = ttk.Frame(outer)
        bar.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self.status_label = ttk.Label(bar, text="Ready", foreground="#374151")
        self.status_label.pack(side="left")
        self.usage_label = ttk.Label(bar, text="", foreground="#6b7280")
        self.usage_label.pack(side="right")
        self.jobs_label = ttk.Label(bar, text="", foreground="#1d4ed8")
        self.jobs_label.pack(side="right", padx=12)

    # ================================================================ logging / queue

    def session_log(self, who: str, kind: str, text: str) -> None:
        if self.session_dir:
            try:
                with open(self.session_dir / "transcript.txt", "a", encoding="utf-8") as fh:
                    fh.write(f"[{datetime.now():%H:%M:%S}] [{who}] {kind.upper()}: {text.strip()}\n")
            except OSError:
                pass

    def _log(self, kind: str, text: str) -> None:
        write_transcript(self.general, kind, text)
        self.session_log("General", kind, text)

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _handle(self, msg: tuple) -> None:
        kind = msg[0]
        job = self.jobs.get(msg[1]) if kind.startswith("j") else None
        if kind == "log":
            self._log(msg[1], msg[2])
        elif kind == "status":
            self.status_label.config(text=msg[1])
        elif kind == "jlog" and job:
            job.log(msg[2], msg[3])
        elif kind == "jstatus" and job:
            job.status_label.config(text=msg[2])
        elif kind == "jtiming" and job:
            job.timing_label.config(text=msg[2])
        elif kind == "jdone" and job:
            self._finish_job(job, *msg[2:])
        elif kind == "usage":
            self._update_usage()
        elif kind == "rec":
            self.rec_label.config(text=f"● Recording… {msg[1]} actions, {msg[2]} frames", foreground="#b91c1c")
        elif kind == "approve":
            _, text, event, box = msg
            holder = tk.Toplevel(self.root)
            holder.withdraw()
            holder.attributes("-topmost", True)
            box["ok"] = messagebox.askyesno("Approve action?", text, parent=holder)
            holder.destroy()
            event.set()
        elif kind == "analysis":
            self._finish_analysis(msg[1])
        elif kind == "fail":
            self._log("error", msg[1])
            self.analyzing = False
            self._update_controls()
        elif kind == "hotkey_stop":
            if self.recording_active:
                self.on_stop_record()
            self.stop_all()

    def approve_from_worker(self, text: str, agent: Agent) -> bool:
        event, box = threading.Event(), {"ok": False}
        self.q.put(("approve", text, event, box))
        while not event.wait(0.2):
            if agent.stopped:
                return False
        return box["ok"]

    def _update_usage(self) -> None:
        totals = dict(self.closed_totals)
        for agent in [self.analyzer] + [j.agent for j in self.jobs.values()]:
            for k in totals:
                totals[k] += agent.totals.get(k, 0)
        self.usage_label.config(text=f"Tokens — in: {totals['input'] + totals['cache_read'] + totals['cache_write']:,}"
                                     f" (cached {totals['cache_read']:,}) · out: {totals['output']:,}")

    # ================================================================ window selection

    def _own_hwnds(self) -> set[int]:
        hwnds = {winapi.root_of(self.root.winfo_id())}
        if self.overlay is not None:
            hwnds.add(winapi.root_of(self.overlay.winfo_id()))
        return hwnds

    def job_for_hwnd(self, hwnd: int) -> Job | None:
        return next((j for j in self.jobs.values() if j.target.hwnd == hwnd), None)

    def _window_label(self, w: winapi.WindowInfo) -> str:
        job = self.job_for_hwnd(w.hwnd)
        mark = "▶ " if job and job.running else ("• " if job else "   ")
        return mark + w.label

    def refresh_windows(self, select_hwnds: list[int] | None = None) -> None:
        keep = set(select_hwnds) if select_hwnds is not None else {w.hwnd for w in self._selected_windows()}
        self.windows = winapi.list_windows(exclude=self._own_hwnds())
        self._render_window_list(keep)

    def _render_window_list(self, keep: set[int] | None = None) -> None:
        if keep is None:
            keep = {w.hwnd for w in self._selected_windows()}
        top = self.win_list.yview()[0]
        self.win_list.delete(0, "end")
        for i, w in enumerate(self.windows):
            self.win_list.insert("end", self._window_label(w))
            if w.hwnd in keep:
                self.win_list.selection_set(i)
        self.win_list.yview_moveto(top)
        self._sync_input_mode()

    def _selected_windows(self) -> list[winapi.WindowInfo]:
        try:
            return [self.windows[i] for i in self.win_list.curselection() if i < len(self.windows)]
        except (tk.TclError, AttributeError):
            return []

    def _on_window_selected(self) -> None:
        sel = self._selected_windows()
        if len(sel) == 1:
            self.status_label.config(text=f"Target: {sel[0].title}")
        elif sel:
            self.status_label.config(text=f"{len(sel)} windows selected — Run starts a job in each.")
        self._sync_input_mode()
        job = self.job_for_hwnd(sel[0].hwnd) if len(sel) == 1 else None
        if job:
            self.notebook.select(job.frame)

    def _require_targets(self, single: bool = False) -> list[winapi.WindowInfo]:
        sel = self._selected_windows()
        if not sel:
            messagebox.showinfo(APP_NAME, "Select the window(s) you want me to work in first.", parent=self.root)
            return []
        if single and len(sel) > 1:
            messagebox.showinfo(APP_NAME, "Select just one window for this.", parent=self.root)
            return []
        alive = [w for w in sel if winapi.is_window(w.hwnd)]
        if len(alive) < len(sel):
            messagebox.showwarning(APP_NAME, "Some selected windows have been closed. I refreshed the list.",
                                   parent=self.root)
            self.refresh_windows()
            return []
        for w in alive:
            w.title = winapi.window_title(w.hwnd) or w.title
        return alive

    def hover_pick(self) -> None:
        self.btn_pick.config(state="disabled")

        def countdown(n: int) -> None:
            if n > 0:
                self.status_label.config(text=f"Hover the mouse over the window you want… {n}")
                self.root.after(1000, countdown, n - 1)
                return
            hwnd = winapi.window_at(*winapi.cursor_pos())
            self.btn_pick.config(state="normal")
            if hwnd in self._own_hwnds():
                self.status_label.config(text="That's WindowPilot itself — try again over another window.")
                return
            self.refresh_windows(select_hwnds=[hwnd])
            sel = self._selected_windows()
            self.status_label.config(text=f"Target: {sel[0].title}" if sel
                                     else "Couldn't identify that window — pick it from the list.")
            self._on_window_selected()

        countdown(3)

    def show_target(self) -> None:
        for w in self._require_targets():
            winapi.bring_to_front(w.hwnd)
            winapi.flash(w.hwnd)

    # ---------------------------------------------------------------- input mode

    def _input_mode(self) -> str:
        label = self.mode_var.get()
        return next((k for k, v in INPUT_MODE_LABELS.items() if v == label), "takeover")

    def _sync_input_mode(self) -> None:
        sel = self._selected_windows()
        if sel and sel[0].exe:
            mode = self.cfg.input_modes.get(sel[0].exe.lower(), "takeover")
            self.mode_var.set(INPUT_MODE_LABELS.get(mode, "Take over"))

    def _on_input_mode(self) -> None:
        mode = self._input_mode()
        exes = sorted({w.exe for w in self._selected_windows() if w.exe})
        for exe in exes:
            self.cfg.input_modes[exe.lower()] = mode
        self.cfg.save()
        self._log("status", INPUT_MODE_HINTS[mode] + (f" (remembered for {', '.join(exes)})" if exes else ""))

    def _on_speed(self) -> None:
        label = self.speed_var.get()
        self.cfg.speed = next(k for k, p in SPEED_PRESETS.items() if p["label"] == label)
        self.cfg.save()
        self.status_label.config(text={"fast": "Fast: quickest steps; may need an extra retry now and then.",
                                       "balanced": "Balanced: good speed with reliable clicks.",
                                       "careful": "Careful: slowest, most thorough — for tricky apps."}[self.cfg.speed])

    # ================================================================ shared state: overlay / hotkey / controls

    def _running_jobs(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.running]

    def _update_controls(self) -> None:
        running = self._running_jobs()
        real_input_jobs = [j for j in running if j.mode != "background"]
        self.btn_stop_all.config(state="normal" if running else "disabled")
        can_record = not self.recording_active and not real_input_jobs
        self.btn_record.config(state="normal" if can_record else "disabled")
        self.btn_stop_rec.config(state="normal" if self.recording_active else "disabled")
        self.btn_analyze.config(state="normal" if self.recording and not self.analyzing
                                and not self.recording_active else "disabled")
        self.btn_save_task.config(state="normal" if (self.analysis or self.last_prompt) else "disabled")
        self.btn_run.config(state="disabled" if self.recording_active else "normal")
        n = len(running)
        self.jobs_label.config(text=f"▶ {n} job{'s' if n != 1 else ''} running" if n else "")
        self._render_window_list()

        # stop hotkey + banner
        if running or self.recording_active:
            self._start_hotkey()
        else:
            self._stop_hotkey()
        if self.recording_active:
            return  # the recording banner stays up
        if not running:
            self._hide_overlay()
            return
        corner = all(j.mode != "takeover" for j in running)
        if n == 1:
            j = running[0]
            how = {"takeover": "working in", "share": "working (when you pause) in",
                   "background": "working in the background in"}[j.mode]
            text = f"🤖 WindowPilot is {how} “{j.short}” — {STOP_HOTKEY_LABEL} to stop"
        else:
            text = f"🤖 WindowPilot: {n} jobs running — {STOP_HOTKEY_LABEL} stops all"
        if self.overlay is not None and self._overlay_corner == corner:
            self._overlay_label.config(text=text)
        else:
            self._show_overlay(text, color="#1e3a8a" if corner else "#1f2937", corner=corner)

    def _start_hotkey(self) -> None:
        if self.hotkeys is None:
            try:
                self.hotkeys = keyboard.GlobalHotKeys({STOP_HOTKEY: lambda: self.q.put(("hotkey_stop",))})
                self.hotkeys.start()
            except Exception:
                self.hotkeys = None

    def _stop_hotkey(self) -> None:
        if self.hotkeys is not None:
            try:
                self.hotkeys.stop()
            except Exception:
                pass
            self.hotkeys = None

    def _show_overlay(self, text: str, color: str = "#1f2937", corner: bool = False) -> None:
        """Small always-on-top banner that is click-through and invisible to screenshots."""
        self._hide_overlay()
        o = tk.Toplevel(self.root)
        o.overrideredirect(True)
        o.attributes("-topmost", True)
        o.attributes("-alpha", 0.9)
        self._overlay_label = tk.Label(o, text=text, bg=color, fg="white", font=("Segoe UI", 10, "bold"),
                                       padx=16, pady=7)
        self._overlay_label.pack()
        o.update_idletasks()
        if corner:
            o.geometry(f"+{o.winfo_screenwidth() - o.winfo_reqwidth() - 16}"
                       f"+{o.winfo_screenheight() - o.winfo_reqheight() - 64}")
        else:
            o.geometry(f"+{(o.winfo_screenwidth() - o.winfo_reqwidth()) // 2}+6")
        o.update()
        hwnd = winapi.root_of(o.winfo_id())
        winapi.make_click_through(hwnd)
        winapi.exclude_from_capture(hwnd)
        self.overlay, self._overlay_corner = o, corner

    def _hide_overlay(self) -> None:
        if self.overlay is not None:
            self.overlay.destroy()
            self.overlay, self._overlay_corner = None, None

    def _ensure_session(self) -> Path:
        if self.session_dir is None:
            self.session_dir = app_dir() / "sessions" / datetime.now().strftime("%Y%m%d-%H%M%S")
            self.session_dir.mkdir(parents=True, exist_ok=True)
        return self.session_dir

    def _bring_self_forward(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(600, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()
        winapi.flash(winapi.root_of(self.root.winfo_id()))

    # ================================================================ recording

    def on_record(self) -> None:
        targets = self._require_targets(single=True)
        if not targets:
            return
        if any(j.mode != "background" for j in self._running_jobs()):
            messagebox.showinfo(APP_NAME, "Jobs are using the mouse and keyboard right now. Wait for them to "
                                          "finish (or stop them) before recording.", parent=self.root)
            return
        w = targets[0]
        folder = self._ensure_session() / f"recording-{datetime.now():%H%M%S}"
        self.recording, self.analysis = None, None
        self.recording_active = True
        self._update_controls()
        self._show_overlay(f"● Recording “{w.title[:40]}” — press {STOP_HOTKEY_LABEL} or Stop recording when done",
                           color="#b91c1c")
        self.notebook.select(0)
        self._log("status", "Recording started. Work in the window normally; I'm watching your clicks, typing and "
                            "the screen. Please don't type passwords while recording.")
        winapi.bring_to_front(w.hwnd)
        self.recorder.start(w.hwnd, w.title, w.exe, folder)

    def on_stop_record(self) -> None:
        if not self.recording_active:
            return
        self.status_label.config(text="Saving recording…")
        self.root.update_idletasks()
        rec = self.recorder.stop()
        self.recording = rec
        self.recording_active = False
        self._hide_overlay()
        self._update_controls()
        self._bring_self_forward()
        self.notebook.select(0)
        self.rec_label.config(text=f"Recording: {rec.duration:.0f}s, {len(rec.events)} actions, "
                                   f"{len(rec.frames)} frames", foreground="#047857")
        log = rec.event_log().splitlines()
        shown = "\n".join(log[:40]) + (f"\n… and {len(log) - 40} more" if len(log) > 40 else "")
        self._log("status", f"Recording saved to {rec.folder}")
        self._log("action", shown)
        if not rec.events:
            self._log("error", "I didn't see any clicks or typing inside the chosen window.")
        else:
            self._log("status", "Press “Analyze recording” so I can work out what you did.")
        self.status_label.config(text="Ready")

    def on_analyze(self) -> None:
        if not self.recording or self.analyzing:
            return
        if not self.cfg.effective_api_key():
            self.open_settings()
            return
        self.analyzing = True
        self._update_controls()
        self.notebook.select(0)
        self.status_label.config(text="Analyzing your demonstration…")
        rec = self.recording

        def work():
            try:
                self.q.put(("analysis", self.analyzer.analyze_recording(rec)))
            except Exception as e:
                self.q.put(("fail", f"Analysis failed: {type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _finish_analysis(self, text: str) -> None:
        self.analysis = text
        self.analyzing = False
        self._log("claude", text)
        if self.recording and self.recording.folder:
            try:
                (self.recording.folder / "analysis.md").write_text(text, encoding="utf-8")
            except OSError:
                pass
        self.use_ref_var.set(True)
        if not self.prompt.get("1.0", "end").strip():
            self.prompt.insert("1.0", DEMO_PROMPT)
        self._update_controls()
        self.status_label.config(text="Analysis ready — Run it in one or more windows, or 💾 Save as task to keep it.")

    # ================================================================ running jobs

    def on_run(self) -> None:
        if self.recording_active:
            return
        targets = self._require_targets()
        if not targets:
            return
        text = self.prompt.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo(APP_NAME, "Type a task for me first.", parent=self.root)
            return
        if not self.cfg.effective_api_key():
            self.open_settings()
            return
        use_ref = self.use_ref_var.get() and self.recording is not None
        if use_ref and self.analysis is None:
            if not messagebox.askyesno(APP_NAME, "Your recording hasn't been analyzed yet. Run anyway, using just "
                                                 "the recorded frames as a guide?", parent=self.root):
                return
        mode = self._input_mode()
        for w in targets:  # the chosen input mode is remembered for these apps
            if w.exe:
                self.cfg.input_modes[w.exe.lower()] = mode
        self.cfg.save()
        self.last_prompt, self.last_target = text, targets[0]
        started = [job for w in targets
                   if (job := self._start_job(w, text, guide_from_recording(self.recording, self.analysis)
                                              if use_ref else None, None, text, mode))]
        if started:
            self.prompt.delete("1.0", "end")
            self.notebook.select(started[0].frame)
            if len(started) > 1:
                self._log("status", f"Started {len(started)} jobs: " + ", ".join(f"“{j.short}”" for j in started))

    def _start_job(self, w: winapi.WindowInfo, prompt: str, guide, saved: SavedTask | None, display: str,
                   mode: str) -> Job | None:
        job = self.job_for_hwnd(w.hwnd)
        if job and job.running:
            self._log("error", f"I'm already working in “{job.short}”. Stop that job first, or pick another window.")
            return None
        self._ensure_session()
        if job is None:
            job = Job(self, w)
            self.jobs[job.id] = job
        elif saved is not None:
            job.agent.reset()  # saved runs start fresh: faster, cheaper and more predictable
        job.target = w
        job.mode = mode
        job.agent.input_mode = mode
        job.agent.confirm = self.confirm_var.get()
        job.timing_label.config(text="")
        job.status_label.config(text="Starting…")
        job.log("you", display)
        if mode != "takeover":
            job.log("status", "You can keep using your mouse and keyboard. "
                              + ("I'll act in the window directly." if mode == "background"
                                 else "I'll act in short bursts whenever you pause for a second."))
        job.set_state("running")
        takeover_jobs = [j for j in self._running_jobs() if j.mode == "takeover"]
        if len(takeover_jobs) > 1 and not self._multi_tip_shown:
            self._multi_tip_shown = True
            self._log("status", "Several Take over jobs share one mouse, so they take turns acting (each thinks "
                                "in parallel). Background or Share mode is smoother and lets you keep working.")
        self._update_controls()
        learn = saved is not None and self.cfg.learn_from_runs

        def work():
            learned = False
            try:
                status, report = job.agent.run_task(w, prompt, guide)
            except Exception as e:
                status, report = "FAILED", f"{type(e).__name__}: {e}"
            if saved is not None:
                lessons = None
                if learn and status == "DONE" and not job.agent.stopped:
                    lessons = job.agent.learn_lessons(saved.lessons) or None
                    learned = bool(lessons)
                try:
                    self.library.record_run(saved, status, lessons)
                except OSError:
                    pass
            self.q.put(("jdone", job.id, status, report, dict(job.agent.last_stats), saved, learned))

        threading.Thread(target=work, daemon=True).start()
        return job

    def _finish_job(self, job: Job, status: str, report: str, stats: dict, saved: SavedTask | None,
                    learned: bool) -> None:
        label = RESULT_LABELS.get(status, status)
        secs = stats.get("seconds", 0)
        took = f"{stats.get('steps', 0)} steps in {int(secs // 60)}m {int(secs % 60)}s"
        job.log("result", f"{label}  ({took})\n{report}")
        job.set_state(status if status in STATE_ICONS else "idle")
        job.status_label.config(text=f"{label} — type a follow-up and press Run to continue here.")
        if learned:
            job.log("status", f"Updated “{saved.name}” with tips so the next run is faster.")
        if saved is not None:
            self.refresh_saved(select_id=saved.id)
        self._log("status", f"{label}: “{job.short}” ({took})")
        notify(f"{APP_NAME} — {job.short}: {label}", report)
        self._update_controls()
        if not self._running_jobs() and job.mode == "takeover":
            self._bring_self_forward()
            self.notebook.select(job.frame)
            self.refresh_windows(select_hwnds=[job.target.hwnd])
            self.prompt.focus_set()
        else:  # you're busy elsewhere or other jobs are running: just flash the taskbar button
            winapi.flash(winapi.root_of(self.root.winfo_id()))

    def stop_all(self) -> None:
        running = self._running_jobs()
        for j in running:
            j.stop()
        if running:
            self._log("status", f"Stopping {len(running)} job{'s' if len(running) != 1 else ''}…")

    def reset_job(self, job: Job) -> None:
        if job.running:
            return
        job.agent.reset()
        job.set_state("idle")
        job.status_label.config(text="Ready")
        job.log("status", "Started a new conversation (I've forgotten the previous tasks in this window).")

    def close_job(self, job: Job) -> None:
        if job.running:
            return
        for k in self.closed_totals:
            self.closed_totals[k] += job.agent.totals.get(k, 0)
        self.notebook.forget(job.frame)
        job.frame.destroy()
        self.jobs.pop(job.id, None)
        self._update_controls()

    # ================================================================ saved tasks

    def refresh_saved(self, select_id: str | None = None) -> None:
        current = self._selected_saved()
        keep = select_id or (current.id if current else None)
        self.saved = self.library.list()
        self.saved_combo["values"] = [t.label for t in self.saved] or ["(no saved tasks yet)"]
        idx = next((i for i, t in enumerate(self.saved) if t.id == keep), 0)
        self.saved_combo.current(idx)

    def _selected_saved(self) -> SavedTask | None:
        try:
            idx = self.saved_combo.current()
        except (tk.TclError, AttributeError):
            return None
        return self.saved[idx] if 0 <= idx < len(getattr(self, "saved", [])) else None

    def _match_window(self, task: SavedTask) -> winapi.WindowInfo | None:
        """Find the window a saved task should run in when no window is selected."""
        exe = task.app_exe.lower()
        self.refresh_windows(select_hwnds=[])
        cands = [w for w in self.windows if exe and w.exe.lower() == exe
                 and not (self.job_for_hwnd(w.hwnd) and self.job_for_hwnd(w.hwnd).running)]
        hint = task.title_hint.lower()
        if hint:
            cands = [w for w in cands if hint in w.title.lower()] or cands
        if cands:
            choice = cands[0]
            self.refresh_windows(select_hwnds=[choice.hwnd])
            if len(cands) > 1:
                self._log("status", f"Several {task.app_exe} windows are open — using “{choice.title}”. "
                                    "Select several windows first to run it in each of them.")
            return choice
        messagebox.showinfo(APP_NAME, f"Open {task.app_exe or 'the app'} first (the app this task was taught "
                                      "in), or select a window, then press Run saved task again.", parent=self.root)
        return None

    def on_run_saved(self) -> None:
        task = self._selected_saved()
        if not task:
            messagebox.showinfo(APP_NAME, "No saved task selected. Teach me a task, then press 💾 Save as task.",
                                parent=self.root)
            return
        if self.recording_active:
            return
        if not self.cfg.effective_api_key():
            self.open_settings()
            return
        targets = self._require_targets() if self._selected_windows() else []
        if not targets:
            if self._selected_windows():
                return
            w = self._match_window(task)
            if not w:
                return
            targets = [w]
        exe = task.app_exe.lower()
        other = [w for w in targets if exe and w.exe.lower() != exe]
        if other and not messagebox.askyesno(
                APP_NAME, f"“{task.name}” was taught in {task.app_exe}, but "
                          + ", ".join(f"“{w.title[:30]}”" for w in other)
                          + " is a different app.\n\nRun it there anyway?", parent=self.root):
            return
        extra = self.prompt.get("1.0", "end").strip()
        prompt = task.instructions + (f"\n\nExtra instructions for this run: {extra}" if extra else "")
        display = f"Run saved task “{task.name}”" + (f" — {extra}" if extra else "")
        started = []
        for w in targets:
            mode = self.cfg.input_modes.get(w.exe.lower(), self._input_mode()) if w.exe else self._input_mode()
            job = self._start_job(w, prompt, task.to_guide(), task, display, mode)
            if job:
                started.append(job)
        if started:
            self.prompt.delete("1.0", "end")
            self.notebook.select(started[0].frame)
            self.last_target = started[0].target

    def on_save_task(self) -> None:
        target = self.last_target or next(iter(self._selected_windows()), None)
        if self.recording is not None and self.analysis:
            app_exe, title = self.recording.exe, self.recording.title
        else:
            app_exe, title = (target.exe, target.title) if target else ("", "")
        self._task_dialog(None, app_exe=app_exe, title=title)

    def on_edit_saved(self) -> None:
        task = self._selected_saved()
        if task:
            self._task_dialog(task)

    def on_delete_saved(self) -> None:
        task = self._selected_saved()
        if not task:
            return
        if any(j.running and getattr(j.agent, "_task_text", "") and task.instructions in j.agent._task_text
               for j in self.jobs.values()):
            messagebox.showinfo(APP_NAME, "That task is running right now. Delete it when it's finished.",
                                parent=self.root)
            return
        if messagebox.askyesno(APP_NAME, f"Delete the saved task “{task.name}”?", parent=self.root):
            self.library.delete(task)
            self.refresh_saved()
            self._log("status", f"Deleted saved task “{task.name}”.")

    def _task_dialog(self, task: SavedTask | None, app_exe: str = "", title: str = "") -> None:
        """Create (task=None) or edit a saved task."""
        new = task is None
        dlg = tk.Toplevel(self.root)
        dlg.title("Save task" if new else f"Edit “{task.name}”")
        dlg.transient(self.root)
        dlg.geometry("640x640")
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)

        if new:
            name0 = _guess_name(self.analysis or "", self.last_prompt)
            instr0 = self.last_prompt or (DEMO_PROMPT if self.analysis else "")
            analysis0, lessons0 = self.analysis or "", ""
        else:
            name0, instr0, analysis0, lessons0 = task.name, task.instructions, task.analysis, task.lessons
            app_exe = task.app_exe

        ttk.Label(frm, text="Name").grid(row=0, column=0, sticky="w")
        name_var = tk.StringVar(value=name0)
        ttk.Entry(frm, textvariable=name_var).grid(row=1, column=0, sticky="ew")
        ttk.Label(frm, text=f"App: {app_exe or 'any'}", foreground="#6b7280").grid(row=2, column=0, sticky="w")

        def box(row, label, value, height):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", pady=(10, 0))
            t = tk.Text(frm, height=height, wrap="word", font=("Segoe UI", 9), relief="solid", bd=1)
            t.grid(row=row + 1, column=0, sticky="nsew")
            t.insert("1.0", value)
            return t

        instr = box(3, "Instructions (what to do each time you run it)", instr0, 4)
        analysis = box(5, "How it's done — from the demonstration (edit to correct anything)", analysis0, 10)
        lessons = box(7, "Tips learned from past runs (updated automatically after successful runs)", lessons0, 5)
        frm.rowconfigure(6, weight=1)
        n_frames = len(reference_frames(self.recording)) if new and self.recording is not None and self.analysis \
            else (len(task.frames) if task else 0)
        ttk.Label(frm, text=f"Reference screenshots kept: {n_frames}", foreground="#6b7280").grid(
            row=9, column=0, sticky="w", pady=(6, 0))

        def save():
            name = name_var.get().strip()
            text = instr.get("1.0", "end").strip()
            if not name or not text:
                messagebox.showerror("Save task", "Please give the task a name and instructions.", parent=dlg)
                return
            if new:
                frames = reference_frames(self.recording) if self.recording is not None and self.analysis else []
                t = self.library.create(name, text, analysis.get("1.0", "end").strip(), app_exe, title, frames)
                t.lessons = lessons.get("1.0", "end").strip()
                self.library.save(t)
                self._log("status", f"Saved task “{name}”. Run it any time from ★ Saved tasks.")
            else:
                task.name, task.instructions = name, text
                task.analysis = analysis.get("1.0", "end").strip()
                task.lessons = lessons.get("1.0", "end").strip()
                self.library.save(task)
                t = task
                self._log("status", f"Updated saved task “{name}”.")
            dlg.destroy()
            self.refresh_saved(select_id=t.id)

        btns = ttk.Frame(frm)
        btns.grid(row=10, column=0, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Save", command=save).pack(side="right")
        dlg.grab_set()

    # ================================================================ settings / help / exit

    def open_settings(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Settings")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)

        key_var = tk.StringVar(value=self.cfg.api_key)
        model_var = tk.StringVar(value=self.cfg.model)
        turns_var = tk.IntVar(value=self.cfg.max_turns)
        learn_var = tk.BooleanVar(value=self.cfg.learn_from_runs)
        idle_var = tk.DoubleVar(value=self.cfg.share_idle_seconds)

        ttk.Label(frm, text="Anthropic API key").grid(row=0, column=0, sticky="w")
        key_entry = ttk.Entry(frm, textvariable=key_var, width=52, show="•")
        key_entry.grid(row=0, column=1, sticky="ew", pady=3)
        env = " (ANTHROPIC_API_KEY env var is set and will be used if this is empty)" \
            if os.environ.get("ANTHROPIC_API_KEY") else ""
        ttk.Label(frm, text="Get one at console.anthropic.com" + env, foreground="#6b7280",
                  wraplength=380).grid(row=1, column=1, sticky="w")
        ttk.Label(frm, text="Model").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(frm, textvariable=model_var, values=MODELS, width=30).grid(row=2, column=1, sticky="w",
                                                                                pady=(10, 0))
        ttk.Label(frm, text="Sonnet: best balance · Opus: hardest tasks · Haiku: cheapest/fastest",
                  foreground="#6b7280").grid(row=3, column=1, sticky="w")
        ttk.Label(frm, text="Max steps per task").grid(row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Spinbox(frm, from_=5, to=2000, textvariable=turns_var, width=8).grid(row=4, column=1, sticky="w",
                                                                                 pady=(10, 0))
        ttk.Checkbutton(frm, text="Improve saved tasks with tips after each successful run",
                        variable=learn_var).grid(row=5, column=1, sticky="w", pady=(6, 0))
        ttk.Label(frm, text="Share mode: pause needed (s)").grid(row=6, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(frm, from_=0.3, to=10.0, increment=0.1, textvariable=idle_var, width=8).grid(
            row=6, column=1, sticky="w", pady=(6, 0))

        def save():
            try:
                self.cfg.api_key = key_var.get().strip()
                self.cfg.model = model_var.get().strip() or MODELS[0]
                self.cfg.max_turns = max(1, int(turns_var.get()))
                self.cfg.learn_from_runs = bool(learn_var.get())
                self.cfg.share_idle_seconds = max(0.3, float(idle_var.get()))
            except (tk.TclError, ValueError):
                messagebox.showerror("Settings", "Please enter valid numbers.", parent=dlg)
                return
            self.cfg.save()
            dlg.destroy()
            self.status_label.config(text=f"Settings saved — model: {self.cfg.model}")

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Save", command=save).pack(side="right")
        key_entry.focus_set()
        dlg.grab_set()

    def show_help(self) -> None:
        messagebox.showinfo(f"How to use {APP_NAME}", (
            "1. Select the window(s) I should work in. Ctrl-click or Shift-click to select several — each one gets "
            "its own job tab, and jobs run at the same time.\n\n"
            "2. Optional — teach me: select one window, press Record, do the task, press Stop recording, then "
            "Analyze.\n\n"
            "3. Type a task and press Run. The same task starts in every selected window. For different tasks, "
            "select one window, type its task, Run; then select the next window, type its task, Run.\n\n"
            "Each job tab shows its progress and has its own Stop button. Type a follow-up and Run again with the "
            "same window selected to continue that job's conversation. "
            f"{STOP_HOTKEY_LABEL} or Stop all halts everything.\n\n"
            "Input: Take over uses your mouse (several Take over jobs take turns). Share lets you keep working — "
            "jobs act in blinks while you pause. Background gives each job its own invisible mouse and keyboard "
            "(classic apps only), so background jobs truly run side by side. The choice is remembered per app.\n\n"
            "Saved tasks: press 💾 Save as task after analyzing a recording or a successful run. Later, select "
            "one or more windows and press Run saved task (or select none and I'll find the app).\n\n"
            "Speed: Fast thinks less per step; Careful is slowest but most thorough."), parent=self.root)

    def show_about(self) -> None:
        messagebox.showinfo(f"About {APP_NAME}", f"{APP_NAME}  v{__version__}\nReleased {RELEASE_DATE}\n\n"
                            "An AI assistant that works inside a Windows app window you choose.\n"
                            "Open source under the MIT License.\n"
                            f"Model: {self.cfg.model}\nData folder: {app_dir()}", parent=self.root)

    def show_whats_new(self) -> None:
        notes = _changelog_section(__version__) or "No release notes found."
        dlg = tk.Toplevel(self.root)
        dlg.title(f"What's new in v{__version__}")
        dlg.transient(self.root)
        dlg.geometry("560x420")
        box = scrolledtext.ScrolledText(dlg, wrap="word", font=("Segoe UI", 10), padx=10, pady=8)
        box.pack(fill="both", expand=True)
        box.insert("1.0", notes)
        box.configure(state="disabled")
        ttk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=6)

    def on_close(self) -> None:
        running = self._running_jobs()
        if running and not messagebox.askyesno(
                APP_NAME, f"{len(running)} job{'s are' if len(running) != 1 else ' is'} running. Stop and quit?",
                parent=self.root):
            return
        for j in running:
            j.agent.stop()
        if self.recording_active:
            try:
                self.recorder.stop()
            except Exception:
                pass
        self._stop_hotkey()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def _guess_name(analysis: str, prompt: str) -> str:
    """A default task name: the first line of the analysis' Goal section, else the prompt."""
    lines = analysis.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("## goal"):
            for nxt in lines[i + 1:]:
                if nxt.strip() and not nxt.startswith("#"):
                    text = nxt.strip().lstrip("-* ").rstrip(".")
                    return text if len(text) <= 60 else text[:57] + "..."
    text = " ".join(prompt.split())
    if not text:
        return "My task"
    return text if len(text) <= 60 else text[:57] + "..."



def _changelog_section(version: str) -> str:
    from changelog import section
    return section(version, plain=True)
