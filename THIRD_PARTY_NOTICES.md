# Third-party software

WindowPilot is MIT-licensed (see LICENSE). It uses these open-source libraries, which keep their own licenses.
The built `.exe` bundles them; the full license texts are in each project's repository.

| Library | Used for | License |
|---|---|---|
| anthropic | Claude API client | MIT |
| mss | Screen capture | MIT |
| Pillow | Image handling | MIT-CMU (HPND) |
| pynput | Recording clicks/keys, stop hotkey | LGPL-3.0 |
| psutil | Showing app names | BSD-3-Clause |
| winotify | Windows notifications | MIT |
| uiautomation | Naming clicked buttons in recordings | Apache-2.0 |
| PyInstaller (build only) | Packaging the .exe | GPL-2.0 with bootloader exception |

pynput is LGPL-3.0: you may replace it with your own modified version by running WindowPilot from source
(`run.bat`) or rebuilding the .exe with `build_exe.bat`, both of which use the full source in this repository.
