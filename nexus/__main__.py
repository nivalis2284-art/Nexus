"""Entry point: `python -m nexus`."""

from .web.server import main

raise SystemExit(main())
