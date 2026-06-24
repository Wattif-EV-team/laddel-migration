"""Logging configuration and helpers for laddel-migration.

The console is meant to read like a *progress feed*: no timestamps, colour by
level, and a small emoji per line. Bulky objects (payloads, SQL) belong at DEBUG
so they land in the file but stay out of the way on screen — except on errors,
where the offending object is shown. The file handler keeps the full, plain
(ASCII-safe, no colour) timestamped record as the durable audit trail.

A ``--plain`` mode strips all colour and non-ASCII output so coding agents, CI
and unit tests get stable, parse-friendly text.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Durable, greppable file format: timestamp + level + logger + message.
_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

_RESET = "\033[0m"

# levelno -> (ANSI colour, emoji icon, ASCII tag used in --plain for WARN+).
_LEVEL_STYLES: dict[int, tuple[str, str, str]] = {
    logging.DEBUG: ("\033[2m", "🔍", "DEBUG"),
    logging.INFO: ("\033[36m", "•", "INFO"),
    logging.WARNING: ("\033[33m", "⚠️", "WARNING"),
    logging.ERROR: ("\033[31m", "❌", "ERROR"),
    logging.CRITICAL: ("\033[1;91m", "🔥", "CRITICAL"),
}

# Block-letter glyphs for the start-up banner (5 rows, fixed width, pure ASCII).
_BANNER_GLYPHS: dict[str, tuple[str, str, str, str, str]] = {
    "L": ("#    ", "#    ", "#    ", "#    ", "#####"),
    "A": (" ### ", "#   #", "#####", "#   #", "#   #"),
    "D": ("#### ", "#   #", "#   #", "#   #", "#### "),
    "M": ("#   #", "## ##", "# # #", "#   #", "#   #"),
    "I": ("#####", "  #  ", "  #  ", "  #  ", "#####"),
    "G": (" ####", "#    ", "#  ##", "#   #", " ####"),
}


class _ConsoleFormatter(logging.Formatter):
    """Format records for the console: ``<icon> <message>``, coloured by level.

    No timestamp and no logger name — the console is a progress feed. With
    ``use_emoji`` off (plain mode) the icon is dropped and WARNING and above get
    an ASCII ``LEVEL:`` prefix instead, so severity is never lost.
    """

    def __init__(self, *, use_color: bool, use_emoji: bool) -> None:
        super().__init__()
        self._use_color = use_color
        self._use_emoji = use_emoji

    def format(self, record: logging.LogRecord) -> str:
        color, emoji, tag = _LEVEL_STYLES.get(record.levelno, ("", "", record.levelname))
        message = record.getMessage()
        icon = getattr(record, "icon", emoji)

        if self._use_emoji and icon:
            prefix = f"{icon} "
        elif not self._use_emoji and record.levelno >= logging.WARNING:
            prefix = f"{tag}: "
        else:
            prefix = ""

        line = f"{prefix}{message}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        if self._use_color and color:
            line = f"{color}{line}{_RESET}"
        return line


def _stderr_supports_color() -> bool:
    """Return ``True`` only for a real TTY with colour not disabled via env."""
    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(sys.stderr, "isatty", None)
    return bool(isatty and isatty())


# The full set of non-ASCII glyphs we might emit, used to probe a stream's codec.
_UNICODE_PROBE = "•→✅⚠️❌🔄✨📋🏁🚀📊🔍🔥"


def supports_unicode(stream: Any | None = None) -> bool:
    """Return ``True`` if ``stream`` (default stdout) can encode our glyphs.

    Windows consoles often default to cp1252, which cannot encode emoji — using
    them there raises ``UnicodeEncodeError``. Callers use this to fall back to
    ASCII automatically rather than crash.
    """
    target = stream if stream is not None else sys.stdout
    encoding = getattr(target, "encoding", None)
    if not encoding:
        return False
    try:
        _UNICODE_PROBE.encode(encoding)
    except LookupError, UnicodeError:
        return False
    return True


def harden_streams() -> None:
    """Make stdout/stderr tolerate un-encodable characters instead of crashing.

    Source data may contain characters outside the console codec; switching the
    error handler to ``backslashreplace`` keeps the process alive (rendering them
    as escapes) rather than aborting a long migration on a single odd byte.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(errors="backslashreplace")


def _get_or_add_console(root: logging.Logger) -> logging.StreamHandler:
    """Return the single tagged console handler, creating it if absent."""
    for handler in root.handlers:
        if getattr(handler, "_ladmig_console", False):
            return handler  # type: ignore[return-value]
    console = logging.StreamHandler()
    console._ladmig_console = True  # type: ignore[attr-defined]
    root.addHandler(console)
    return console


def configure_logging(
    level: int | str = logging.INFO,
    *,
    log_to_file: bool = False,
    script: str | None = None,
    log_dir: str | Path = "log",
    plain: bool = False,
) -> Path | None:
    """Configure the root logger with a console handler (and optionally a file).

    Safe to call multiple times; the single console handler is reused and its
    level/formatter refreshed. ``plain`` disables colour and emoji (and any
    non-ASCII decoration). When ``log_to_file`` is true a timestamped file
    handler is added under ``log_dir/YYYY-MM-DD/`` capturing DEBUG and above —
    the durable, plain audit trail for a migration run.

    Returns the log file path when one was created, else ``None``.
    """
    root = logging.getLogger()
    # The console handler enforces ``level``; the root must sit at DEBUG when a
    # file is attached so verbose records still reach the file handler.
    root.setLevel(logging.DEBUG if log_to_file else level)

    harden_streams()
    console = _get_or_add_console(root)
    console.setLevel(level)
    console.setFormatter(
        _ConsoleFormatter(
            use_color=(not plain) and _stderr_supports_color(),
            use_emoji=(not plain) and supports_unicode(sys.stderr),
        )
    )

    if not log_to_file:
        return None

    folder = Path(log_dir) / datetime.now().strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    name = script or "ladmig"
    log_path = folder / f"{name}.{datetime.now().strftime('%H%M%S')}.log"

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    return log_path


def render_banner(text: str = "LADMIG") -> str:
    """Render ``text`` as a fixed-width, pure-ASCII block-letter banner."""
    glyphs = [_BANNER_GLYPHS[ch] for ch in text.upper() if ch in _BANNER_GLYPHS]
    if not glyphs:
        return text
    return "\n".join("  ".join(glyph[row] for glyph in glyphs) for row in range(5))


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger for the given name (defaults to the package logger)."""
    return logging.getLogger(name if name else "laddel_migration")


def mapping_breadcrumb(logger: logging.Logger, mapping_key: str, target_id: object) -> None:
    """Emit the machine-readable mapping breadcrumb BEFORE persisting a mapping.

    This single line is the last line of defence against losing track of a
    created resource: it is grep-able (stable ``KEY|field=value`` format) so a
    lost mapping write can be reconciled or rebuilt from the log afterwards.
    Always log it immediately after the target system returns the new id and
    before the mapping INSERT.
    """
    logger.info("MAPPING_RECORD|mapping_key=%s|target_id=%s", mapping_key, target_id)


def log_field_diffs(
    logger: logging.Logger,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, tuple[Any, Any]]:
    """Log per-field ``old -> new`` changes and return them.

    Compares every key we intend to write (``after``) against its prior value in
    ``before`` (absent keys count as ``None``). Only changed fields are logged,
    giving a durable record of exactly what an update altered.
    """
    changes: dict[str, tuple[Any, Any]] = {}
    for key, new in after.items():
        old = before.get(key)
        if old != new:
            changes[key] = (old, new)
            logger.info("  %s: %r -> %r", key, old, new)
    return changes
