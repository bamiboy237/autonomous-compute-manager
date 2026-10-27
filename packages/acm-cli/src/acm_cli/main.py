"""acmctl entrypoint."""

import sys

from acm_cli import __version__


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--version"]:
        print(f"acmctl {__version__}")
        return 0
    print("acmctl: no commands yet. See docs/CLI.md once THE-60 lands.", file=sys.stderr)
    return 2
