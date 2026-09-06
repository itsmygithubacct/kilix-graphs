"""Write an output file without replacing anything that is already there.

Every path that writes a file the user did not just name in a save dialog
goes through here. The viewers' `w` key used to write `<stem>.svg` and
`.png` beside the source with a bare `write_text`, which replaced whatever
was there, wrote through a symlink onto some other file, and overwrote the
source itself when the source was already the `.svg`; `-o` did the same
with a name the user typed once and may have forgotten. A drawing tool
must not be the thing that destroys a file.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path


class OutputExists(OSError):
    """The target exists, or is a symlink, and was left alone."""


def write_new(
    target: Path | str,
    data: bytes | str,
    *,
    force: bool = False,
    protect: Iterable[Path | str] = (),
) -> Path:
    """Create `target` holding `data`; never replace what is already there.

    The file is created with O_EXCL and O_NOFOLLOW, so an existing file, a
    symlink (dangling or not), and anything a race puts there first are all
    refused. With `force`, an existing regular file that is not a symlink is
    replaced through an exclusive temporary and a rename; a symlink is still
    refused, because writing through it reaches some other file. Anything in
    `protect` (the inputs) is refused even with `force`.
    """
    path = Path(target)
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    for guarded in protect:
        guarded_path = Path(guarded)
        try:
            if guarded_path.exists() and path.exists() and path.samefile(guarded_path):
                raise OutputExists(f"{path} is the input; not replacing it")
        except OSError as error:
            if isinstance(error, OutputExists):
                raise
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o644)
    except FileExistsError:
        if not force:
            raise OutputExists(f"{path} exists; choose another name or pass --force") from None
        if path.is_symlink() or not path.is_file():
            raise OutputExists(f"{path} is not a regular file; not replacing it") from None
        temporary = path.with_name(f".{path.name}.{os.getpid()}.new")
        descriptor = os.open(temporary, flags, 0o644)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return path
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
    return path
