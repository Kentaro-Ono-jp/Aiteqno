"""Allow ``python -m aiteqno.cli`` to behave like the console command."""

from . import main


raise SystemExit(main())
