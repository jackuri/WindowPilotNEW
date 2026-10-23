# WindowPilot

**Version 1.3.0** · open source under the [MIT License](LICENSE) · see [CHANGELOG.md](CHANGELOG.md) for what changed in each release (also in the app under Help → What's new).

An AI assistant for Windows that works inside **one app window you choose**. Give it a task in plain English, or **show it** what to do by recording yourself, and it will repeat the work until it's finished, then notify you so you can give it the next task.

It is powered by Claude's computer-use capability: Claude sees screenshots of the chosen window and controls the mouse and keyboard inside it.

## Setup (about 5 minutes)

1. Install **Python 3.10+** from python.org. During setup, tick **"Add python.exe to PATH"**.
2. Get an **Anthropic API key** at https://console.anthropic.com (usage is billed per token; see "Cost" below).
3. Unzip WindowPilot anywhere and double-click **`run.bat`**. The first launch installs dependencies (about a minute).
4. Paste your API key into the Settings window that opens.

Optional: run `build_exe.bat` afterwards to produce a standalone `dist\WindowPilot-<version>.exe`.

## Publishing to GitHub

Install Git for Windows (https://git-scm.com/download/win), then double-click **`publish_to_github.bat`**. The first time, it asks for a repository name and whether the repo should be public or private (default: private), then creates the repository. It does this with the GitHub CLI if you have it; otherwise it opens GitHub's "new repository" page for you. You sign in through GitHub's own login window; no tokens to copy.

Each time you push a new version, GitHub Actions automatically builds `WindowPilot-<version>.exe` and publishes it as a **Release**, with that version's notes from CHANGELOG.md. The tag must match `version.py`; the build stops if they differ.

To publish an update, unzip the new version **over** this folder, keeping the hidden `.git` folder, and run `publish_to_github.bat` again. It commits, tags (e.g. `v1.3.0`) and uploads.

**Updating:** replace the folder with the new version (your settings, saved tasks and recordings live in `%APPDATA%\WindowPilot` and are kept). The app tells you when it has been updated.

## Using it

1. **Pick a window** from the dropdown, or click **Hover-pick** and hold your mouse over the window for 3 seconds.
2. **Give it a task** and press **Run** (or Ctrl+Enter), for example:
   - "Fill in the Status column with 'Shipped' for every row where Tracking # is filled in."
   - "Rename each file in this folder view to `invoice-<date>.pdf` using the date shown in its preview."
3. **Or teach it first:**
   1. Press **● Record**, do the task once or twice in the window, then press **Stop recording** (or Ctrl+Shift+F12).
   2. Press **Analyze recording**. Claude describes what you did, the goal it inferred, a step-by-step procedure, and how it will know when the job is finished. Read this carefully and correct anything in your task text.
   3. Press **Run** with a task such as "Repeat what I demonstrated until the list is empty."
4. While it works, a banner at the top of the screen shows progress. **Keep your hands off the mouse and keyboard.** Press **Ctrl+Shift+F12** at any time to stop immediately.
5. When it finishes, needs your input, or gets stuck, you get a **Windows notification** and WindowPilot comes back to the front. Type your next task or answer. It remembers the conversation until you press **New conversation**.

## Several windows at once

Select more than one window in the list (Ctrl-click or Shift-click), type a task and press **Run**: each window gets its own **job tab** and they all work at the same time. For *different* tasks, select one window, type its task, Run; then select the next window, type its task, Run.

- Each tab shows that job's progress, timing and result, and has its own **Stop**, **New conversation** and **Close** buttons. To continue a job, select its window, type a follow-up and press Run.
- **Stop all** (or Ctrl+Shift+F12) halts every job.
- Saved tasks run in every selected window, or WindowPilot finds the right app if none is selected.

How they share your PC: every job *thinks* in parallel (that's most of the time). Only the split-second of acting needs the real mouse and keyboard, so jobs in Take over or Share mode take turns for that. **Background** jobs have their own invisible mouse and keyboard and run fully side by side. For several jobs at once, Background (where the app supports it) or Share works best. With several Take over jobs, the windows take turns coming to the front.

## Saved tasks: teach once, reuse anytime

1. Teach a task (Record → Stop → Analyze), or just run a typed task successfully.
2. Press **💾 Save as task…**, give it a name, and check the instructions and the "how it's done" analysis. Fix anything Claude got wrong; this text is exactly what it follows later.
3. Any time later, pick it under **★ Saved tasks** and press **▶ Run saved task**. WindowPilot finds the matching app window automatically, so you only need that app open.
4. Need a variation? Type it in the task box first (e.g. "only rows 1–20") and it's added as extra instructions for that run.
5. After each successful run, Claude writes a few **tips** (where controls are, shortcuts that worked, pitfalls), and the next run uses them, so saved tasks get faster over time. You can read and edit the tips with **✎ Edit**.

Saved tasks live in `%APPDATA%\WindowPilot\tasks`, one folder each.

## Keep working while the AI works: input modes

Windows has only one real mouse cursor and one keyboard focus, so WindowPilot offers three ways to share them. Pick one with the **Input** selector; it's remembered for each app.

| Mode | How it works | Works with |
|---|---|---|
| **Take over** (default) | Uses your real mouse and keyboard. Hands off while it runs. | Every app; most reliable |
| **Share** | Waits until you pause for about 1 second, does its batch of actions in a blink, then puts your cursor and active window back exactly where they were. | Every app |
| **Background** | The AI gets its **own invisible mouse and keyboard**: clicks and typing go straight to the target window, which can sit behind your other windows. Your cursor never moves. | Many classic Windows apps (Notepad, many business/legacy tools, dialogs). Usually **not** browsers, Electron apps, games or newer Store apps. If it doesn't work, Claude stops and suggests switching to Share. |

Notes:
- In Share and Background modes the window is captured even when it's covered, so it can stay behind your work. **Don't minimize it.** If an app can't be captured while hidden, WindowPilot briefly borrows the screen the same polite way.
- In Background mode, shortcuts with Ctrl/Alt/Shift (like Ctrl+S) still need the real keyboard, so those single steps wait for you to pause, Share-style.
- In Share mode, if you start typing during the split-second burst, a keystroke could land in the AI's window. The pause detection makes this rare; you can lengthen the pause required in Settings.

**Want a truly separate mouse and keyboard for any app?** Run WindowPilot and the app inside a virtual machine: Hyper-V on Windows Pro/Enterprise, or Windows Sandbox for throwaway sessions (it resets when closed). The VM has its own cursor and keyboard, so the AI can use Take over mode inside it while you use your PC normally. Keep the VM window open (not minimized).

## Speed

Most of the time per step is Claude deciding what to do next. The **Speed** selector controls this:

| Mode | What changes | Use it for |
|---|---|---|
| **Fast** | Least thinking per step, smaller JPEG screenshots, 0.1s pause after actions | Simple, repetitive tasks, especially saved ones |
| **Balanced** (default) | Medium thinking, 0.25s pause | Most tasks |
| **Careful** | Full thinking, sharpest screenshots, 0.4s pause | Tricky apps, first runs of new tasks |

In every mode Claude is told to send predictable actions together (click, type, Tab, type, Enter in one go) instead of one per round trip, and a screenshot comes back automatically after each group. The status bar shows how long the last step took, split into Claude's thinking time and action time.

Other speed tips: **Haiku** (Settings → Model) is the fastest model. Maximizing the target window helps Claude find things on the first try. Good task text ("press Ctrl+S after each row") saves exploration.

Turn on **"Ask before each action"** to approve every click and keystroke, which is useful the first time you try a new kind of task.

## Safety built in

- Claude only sees and acts inside the chosen window. Clicks outside it are rejected.
- It is instructed to stop and ask before deleting data, sending messages, purchases, submitting forms, or accepting terms that your task didn't explicitly request, and never to type passwords or payment details.
- On-screen text (web pages, emails, documents) is treated as data, not instructions.
- Hard step limit per task (Settings, default 150) prevents runaway costs.
- Recordings, frames and transcripts are stored locally in `%APPDATA%\WindowPilot\sessions`. **Don't type passwords while recording**, since keystrokes in the target window are logged and sent to Claude for analysis.

## Limitations

- Only the target window is captured. Menus or dialogs that open **outside** its bounds are invisible to Claude; maximizing the window helps.
- Apps running **as administrator** only accept input from another admin process. Run WindowPilot as administrator in that case.
- Games with anti-cheat or exclusive full-screen mode may block simulated input or screen capture.
- It's slower than a person (a few seconds per step), so it's best for tedious, repetitive work you can leave running.

## Cost

Each step sends a screenshot (about 1,500 tokens) plus the conversation. Prompt caching and automatic history checkpoints keep this down. The status bar shows token usage live, so you can check it against Anthropic's pricing page. Haiku is cheapest; Opus is best for complicated apps.

## Files

| File | What it does |
|---|---|
| `main.py` | Entry point |
| `gui.py` | The app window, overlay, stop hotkey and notifications |
| `agent.py` | Claude analysis and the computer-use agent loop |
| `recorder.py` | Watches your clicks, typing and screen during a demonstration |
| `capture.py` | Window screenshots and scaling |
| `win_input.py` | Mouse and keyboard control via Win32 `SendInput` |
| `shared_input.py` | Background input (the AI's own mouse/keyboard) and Share-mode input borrowing |
| `winapi.py` | Window listing, focus and DPI helpers |
| `tasks.py` | Saved-task library |
| `version.py` | Version number. Bump it and add a CHANGELOG.md entry with every release |
| `changelog.py` | Reads release notes from CHANGELOG.md (in-app "What's new" and GitHub Releases) |
| `publish_to_github.bat` | Creates the GitHub repo / uploads each new version |
| `.github/workflows/release.yml` | Builds the .exe and publishes a GitHub Release for every version tag |
| `LICENSE` / `THIRD_PARTY_NOTICES.md` | MIT license / licenses of bundled libraries |
| `config.py` | Settings and speed presets, stored in `%APPDATA%\WindowPilot\config.json` |

## License

WindowPilot is released under the [MIT License](LICENSE): anyone may use, copy, modify and share it, including commercially, as long as the copyright and license notice stay with it. It comes with no warranty. Bundled libraries keep their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
