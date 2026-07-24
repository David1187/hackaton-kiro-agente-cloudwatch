"""
conftest.py — pytest configuration for services/crud_api.

Adds the crud_api package root to sys.path so that `from common import ...`
works without an editable install.  Also pre-sets TABLE_NAME so the
module-level boto3 Table initialisation in repository.py does not fail at
import time.
"""

import os
import sys

# Ensure crud_api root (parent of conftest.py) is on the path
_CRUD_API_ROOT = os.path.dirname(__file__)
if _CRUD_API_ROOT not in sys.path:
    sys.path.insert(0, _CRUD_API_ROOT)

# Required by repository.py at import (module-level Table init)
os.environ.setdefault("TABLE_NAME", "Tasks")
