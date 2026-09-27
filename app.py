#!/usr/bin/env python3
"""Compatibility entry point: python3 app.py."""
import sys
from termiusplus import server

if __name__ == '__main__':
    server.main()
else:
    # Preserve import app and existing integrations that patch server attributes.
    sys.modules[__name__] = server
