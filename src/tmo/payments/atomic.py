"""Write a JSON file such that a crash cannot leave it half-written.

BUG 1, as shipped in tirramind: ``SubscriberStore._save`` called
``Path.write_text``, which truncates the file and then writes. A crash, a
full disk or an OOM kill between those two steps leaves a truncated or empty
``subscribers.json``, and every paying customer's key is gone -- silently,
because the loader catches the parse error and starts from ``{}``.

The fix is the standard one: serialise to a sibling temp file, flush it to
the platter, then ``os.replace``, which is atomic on POSIX and on Windows.
A reader sees either the whole old file or the whole new one, never a
fragment. The directory itself is fsynced too, so the rename survives a
power loss and not merely a process crash.

The file holds live API keys, so it is created 0600 rather than inheriting
whatever the umask happens to be.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

_MODE = 0o600


def write_json(path: Path, data: Any, *, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Serialise before touching the filesystem: a value that cannot be encoded
    # must fail without having disturbed the file that is already there.
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, _MODE)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    # Durability of the rename itself, not just of the bytes.
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


__all__ = ["write_json"]
