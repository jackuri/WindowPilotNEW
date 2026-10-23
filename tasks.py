"""Saved tasks: teach WindowPilot once, then rerun the task any time.

Each task lives in %APPDATA%\\WindowPilot\\tasks\\<id>\\ as task.json plus a few reference frames.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image

from config import app_dir
from version import __version__

MAX_REFERENCE_FRAMES = 6


@dataclass
class Guide:
    """Everything Claude gets to help it repeat a known workflow."""
    name: str = ""
    analysis: str = ""
    lessons: str = ""
    frames: list = field(default_factory=list)  # list of (t, note, PIL.Image)


@dataclass
class SavedTask:
    id: str
    name: str
    instructions: str
    analysis: str = ""
    lessons: str = ""
    app_exe: str = ""
    window_title: str = ""
    created: str = ""
    updated: str = ""
    runs: int = 0
    last_status: str = ""
    frames: list = field(default_factory=list)  # [{"file", "t", "note"}]
    app_version: str = ""  # WindowPilot version that last saved it (for future upgrades of the format)

    @property
    def folder(self) -> Path:
        return TaskLibrary.root() / self.id

    @property
    def label(self) -> str:
        bits = [self.app_exe or "any app"]
        if self.runs:
            bits.append(f"{self.runs} run{'s' if self.runs != 1 else ''}")
        return f"{self.name}   ({', '.join(bits)})"

    @property
    def title_hint(self) -> str:
        """The app-name part of the window title, e.g. 'Excel' from 'Book1 - Excel'."""
        parts = re.split(r"\s[-—–|]\s", self.window_title)
        return parts[-1].strip() if len(parts) > 1 else ""

    def to_guide(self) -> Guide:
        frames = []
        for fr in self.frames:
            try:
                with Image.open(self.folder / fr["file"]) as im:
                    frames.append((fr.get("t", 0.0), fr.get("note", ""), im.convert("RGB").copy()))
            except OSError:
                pass
        return Guide(self.name, self.analysis, self.lessons, frames)


class TaskLibrary:
    _lock = threading.RLock()  # several jobs may finish the same saved task at the same time

    @staticmethod
    def root() -> Path:
        path = app_dir() / "tasks"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list(self) -> list[SavedTask]:
        tasks = []
        for f in self.root().glob("*/task.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                known = {k: v for k, v in data.items() if k in SavedTask.__dataclass_fields__}
                tasks.append(SavedTask(**known))
            except (OSError, ValueError, TypeError):
                continue
        return sorted(tasks, key=lambda t: t.name.lower())

    def create(self, name: str, instructions: str, analysis: str, app_exe: str, window_title: str,
               frames: list | None = None) -> SavedTask:
        """frames: list of (t, note, PIL.Image) to keep as reference screenshots."""
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "task"
        now = datetime.now()
        task = SavedTask(id=f"{slug}-{now:%Y%m%d%H%M%S}", name=name, instructions=instructions,
                         analysis=analysis, app_exe=app_exe, window_title=window_title,
                         created=now.isoformat(timespec="seconds"))
        task.folder.mkdir(parents=True, exist_ok=True)
        for i, (t, note, img) in enumerate((frames or [])[:MAX_REFERENCE_FRAMES]):
            fname = f"frame_{i + 1}.jpg"
            img.convert("RGB").save(task.folder / fname, quality=90)
            task.frames.append({"file": fname, "t": round(float(t), 1), "note": note})
        self.save(task)
        return task

    def save(self, task: SavedTask) -> None:
        with self._lock:
            self._save(task)

    def _save(self, task: SavedTask) -> None:
        task.updated = datetime.now().isoformat(timespec="seconds")
        task.app_version = __version__
        task.folder.mkdir(parents=True, exist_ok=True)
        (task.folder / "task.json").write_text(json.dumps(asdict(task), indent=2), encoding="utf-8")

    def delete(self, task: SavedTask) -> None:
        shutil.rmtree(task.folder, ignore_errors=True)

    def record_run(self, task: SavedTask, status: str, lessons: str | None = None) -> None:
        with self._lock:
            task.runs += 1
            task.last_status = status
            if lessons:
                task.lessons = lessons
            self._save(task)


def reference_frames(recording, k: int = MAX_REFERENCE_FRAMES) -> list:
    """Pick up to k evenly spread frames from a Recording (preferring frames where the user clicked)."""
    frames = [f for f in recording.frames if f.marker] or list(recording.frames)
    if len(frames) > k:
        step = (len(frames) - 1) / (k - 1)
        frames = [frames[round(i * step)] for i in range(k)]
    return [(f.t, f.note, f.image) for f in frames]


def guide_from_recording(recording, analysis: str | None) -> Guide:
    return Guide(name="", analysis=analysis or "", lessons="", frames=reference_frames(recording, 4))
