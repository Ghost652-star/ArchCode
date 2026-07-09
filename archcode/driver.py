from __future__ import annotations

import sys

if sys.platform == "win32":
    from textual.drivers.windows_driver import WindowsDriver as _BaseDriver
else:
    from textual.drivers.linux_driver import LinuxDriver as _BaseDriver


class NoAltScreenDriver(_BaseDriver):
    """退出时保留终端滚动历史，不切换到 alternate screen。"""

    def start_application_mode(self) -> None:
        import os

        try:
            rows = os.get_terminal_size().lines
        except OSError:
            rows = 24
        sys.stdout.write("\n" * rows)
        sys.stdout.flush()
        super().start_application_mode()

    def write(self, data: str) -> None:
        data = data.replace("\x1b[?1049h", "").replace("\x1b[?1049l", "")
        if data:
            super().write(data)
