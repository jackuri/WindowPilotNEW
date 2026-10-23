"""Settings for WindowPilot, stored as JSON in %APPDATA%\\WindowPilot."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_NAME = "WindowPilot"
MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]

# effort: how long Claude thinks per step (None = API default, the most thorough).
# max_long / max_pixels: screenshot size sent to Claude. delay: pause after each action.
SPEED_PRESETS = {
    "fast":     {"label": "Fast",     "effort": "low",    "max_long": 1280, "max_pixels": 800_000,
                 "delay": 0.10, "fmt": "JPEG", "type_delay": 0.002},
    "balanced": {"label": "Balanced", "effort": "medium", "max_long": 1456, "max_pixels": 1_000_000,
                 "delay": 0.25, "fmt": "JPEG", "type_delay": 0.004},
    "careful":  {"label": "Careful",  "effort": None,     "max_long": 1568, "max_pixels": 1_150_000,
                 "delay": 0.40, "fmt": "PNG", "type_delay": 0.006},
}


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Config:
    api_key: str = ""                 # leave empty to use the ANTHROPIC_API_KEY env var
    model: str = MODELS[0]
    speed: str = "balanced"           # key of SPEED_PRESETS
    max_turns: int = 150              # safety limit on agent steps per task
    confirm_each_step: bool = False   # ask before every mouse/keyboard action
    learn_from_runs: bool = True      # update a saved task's tips after each successful run
    share_idle_seconds: float = 1.0   # Share mode: how long you must pause before the AI borrows input
    input_modes: dict = field(default_factory=dict)  # remembered input mode per app (exe name -> mode)
    last_version: str = ""            # last version that ran, to show "what's new" after an update

    @property
    def preset(self) -> dict:
        return SPEED_PRESETS.get(self.speed, SPEED_PRESETS["balanced"])

    @classmethod
    def path(cls) -> Path:
        return app_dir() / "config.json"

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
            for f in fields(cls):
                if f.name in data:
                    setattr(cfg, f.name, type(getattr(cfg, f.name))(data[f.name]))
        except (OSError, ValueError, TypeError):
            pass
        if cfg.speed not in SPEED_PRESETS:
            cfg.speed = "balanced"
        return cfg

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def effective_api_key(self) -> str:
        return self.api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip()
