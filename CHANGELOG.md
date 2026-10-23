# Changelog

All notable changes to WindowPilot. Versions follow MAJOR.MINOR.PATCH: features bump MINOR, fixes bump PATCH.

## 1.3.0 — 2026-10-18
### Added
- **Run several jobs at once**: select multiple windows (Ctrl/Shift-click) to run the same task in each,
  or start a different task in each window. Every job gets its own tab with its own conversation,
  progress, Stop button and follow-ups.
- Saved tasks can run in several selected windows at once.
- **Stop all** button; the stop hotkey (Ctrl+Shift+F12) now stops every job.
- Window list shows which windows have a running (▶) or finished (•) job.
### Changed
- Jobs think in parallel and take turns on the real mouse/keyboard only while acting. Background-mode
  jobs never need the real mouse, so they run fully side by side.
- "Ask before each action" now applies per job (set when the job starts).
- Approval prompts name the job they come from; notifications name the window.

## 1.2.1 — 2026-10-17
### Added
- Released as open source under the **MIT License** (LICENSE), with THIRD_PARTY_NOTICES.md listing bundled libraries.
- License shown in Help → About and bundled inside the built .exe.

## 1.2.0 — 2026-10-16
### Added
- **Input modes** (Input selector, remembered per app):
  - **Share**: the AI acts in quick bursts while you pause, then restores your cursor and active window.
  - **Background**: the AI gets its own invisible mouse and keyboard; works with many classic Windows apps.
- The target window can stay behind your other windows in Share/Background modes (background capture).
- Corner status banner and taskbar flash instead of jumping in front when you're working.
- Setting for how long a pause Share mode waits for.
- Version number in the title bar, Help → About and Help → What's new.
- GitHub publishing: `publish_to_github.bat`, plus automatic .exe builds and Releases for every version tag.
### Fixed
- Share mode could mistake your input right after an AI burst for its own.
- An empty finish message showed raw status text.

## 1.1.0 — 2026-10-15
### Added
- **Saved tasks**: teach once, save, rerun any time; automatic window matching; extra instructions per run.
- Saved tasks learn tips after each successful run, so they get faster over time.
- **Speed modes** (Fast / Balanced / Careful), action batching, faster screenshot encoding.
- Per-step timing in the status bar.
### Fixed
- Crash when sampling reference screenshots for a saved task.

## 1.0.0 — 2026-10-15
### Added
- First release: pick a window, give tasks in plain English, record/analyze demonstrations,
  run until done with notifications, stop hotkey (Ctrl+Shift+F12), approve-each-action mode.
