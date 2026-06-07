"""Tiny console logger with stage banners and color (no extra dependencies)."""
from __future__ import annotations

import os
import sys

_USE_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def dim(t: str) -> str:
    return _c("2", t)


def bold(t: str) -> str:
    return _c("1", t)


def green(t: str) -> str:
    return _c("32", t)


def yellow(t: str) -> str:
    return _c("33", t)


def red(t: str) -> str:
    return _c("31", t)


def cyan(t: str) -> str:
    return _c("36", t)


class Logger:
    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose

    def stage(self, number: int, title: str, tool: str) -> None:
        bar = "─" * 64
        print()
        print(cyan(bar))
        print(cyan(bold(f" STAGE {number} · {title}")) + dim(f"   [{tool}]"))
        print(cyan(bar))

    def info(self, msg: str) -> None:
        print(f"  {msg}")

    def ok(self, msg: str) -> None:
        print(f"  {green('✓')} {msg}")

    def warn(self, msg: str) -> None:
        print(f"  {yellow('!')} {msg}")

    def error(self, msg: str) -> None:
        print(f"  {red('✗')} {msg}")

    def debug(self, msg: str) -> None:
        if self.verbose:
            print(dim(f"    · {msg}"))

    def banner(self, msg: str) -> None:
        print()
        print(bold(msg))
