"""WindowPilot — an AI assistant that works inside a Windows app window you choose."""
import sys


def main() -> None:
    if sys.platform != "win32":
        print("WindowPilot runs on Windows 10/11 only.")
        sys.exit(1)
    import winapi
    winapi.set_dpi_awareness()  # must happen before any window is created
    from gui import App
    App().run()


if __name__ == "__main__":
    main()
