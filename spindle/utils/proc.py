"""Process-group termination shared by adapters that launch long-running
subprocesses with `start_new_session=True` (MakeMKV, HandBrake), so killing a
rip or encode doesn't leave forked helpers holding the drive or a file handle.

`os.killpg`/`os.getpgid` don't exist on Windows; `Process.terminate()`/`.kill()`
are the cross-platform fallback used there instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal


def terminate_process_group(process: asyncio.subprocess.Process) -> None:
    killpg = getattr(os, "killpg", None)
    getpgid = getattr(os, "getpgid", None)
    if killpg is not None and getpgid is not None:
        with contextlib.suppress(ProcessLookupError):
            killpg(getpgid(process.pid), signal.SIGTERM)
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()


def kill_process_group(process: asyncio.subprocess.Process) -> None:
    killpg = getattr(os, "killpg", None)
    getpgid = getattr(os, "getpgid", None)
    if killpg is not None and getpgid is not None:
        with contextlib.suppress(ProcessLookupError):
            killpg(getpgid(process.pid), signal.SIGKILL)
        return
    with contextlib.suppress(ProcessLookupError):
        process.kill()
