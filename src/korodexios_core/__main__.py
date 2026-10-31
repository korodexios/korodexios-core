"""'python -m korodexios_core ...' runs the same console client as 'kdx'.

A service manager starts the daemon this way (an interpreter path plus a module), so the
service keeps working when the console-script shims are not on PATH.
"""

from __future__ import annotations

from .cli.entry import main

raise SystemExit(main())
