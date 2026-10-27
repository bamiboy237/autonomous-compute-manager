"""``python -m acm_core.db upgrade|downgrade [revision]`` (used by ``just migrate``)."""

from __future__ import annotations

import sys

from acm_core.db.migrate import downgrade, upgrade


def main(argv: list[str]) -> int:
    action = argv[0] if argv else "upgrade"
    if action == "upgrade":
        upgrade(revision=argv[1] if len(argv) > 1 else "head")
    elif action == "downgrade":
        downgrade(revision=argv[1] if len(argv) > 1 else "-1")
    else:
        print("usage: python -m acm_core.db [upgrade [rev] | downgrade [rev]]", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
