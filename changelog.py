"""Reads release notes for one version from CHANGELOG.md.

Command line (used by the GitHub release workflow):  python changelog.py 1.2.0 notes.md
"""
from __future__ import annotations

import sys
from pathlib import Path


def section(version: str, plain: bool = False) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # also works inside the .exe
    try:
        text = (base / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip().startswith(version)
            if inside and not plain:
                continue  # GitHub shows the version as the release title already
        if inside:
            if plain:
                line = line.replace("**", "").replace("### ", "").replace("## ", "Version ")
            out.append(line)
    return "\n".join(out).strip()


if __name__ == "__main__":
    notes = section(sys.argv[1]) or f"WindowPilot {sys.argv[1]}"
    Path(sys.argv[2]).write_text(notes + "\n", encoding="utf-8")
