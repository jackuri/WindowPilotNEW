"""Desktop notifications (Windows toast with sound), with a beep as a fallback."""
from __future__ import annotations

from config import APP_NAME


def notify(title: str, message: str) -> None:
    message = " ".join(message.split())
    if len(message) > 240:
        message = message[:237] + "..."
    try:
        from winotify import Notification, audio
        toast = Notification(app_id=APP_NAME, title=title, msg=message or " ")
        toast.set_audio(audio.Default, loop=False)
        toast.show()
        return
    except Exception:
        pass
    try:
        import winsound
        winsound.MessageBeep()
    except Exception:
        pass
