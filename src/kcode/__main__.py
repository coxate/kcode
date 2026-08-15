import sys

from kcode import __version__

if any(argument in {"--version", "-V"} for argument in sys.argv[1:]):
    print(__version__)
    raise SystemExit(0)

from kcode.cli import main  # noqa: E402

raise SystemExit(main())
