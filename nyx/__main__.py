"""Module entry point for ``python -m nyx``.

This delegates directly to the same CLI entry point used by the installed
``nyx`` console script so both invocation modes behave identically.
"""

from __future__ import annotations

from nyx.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
