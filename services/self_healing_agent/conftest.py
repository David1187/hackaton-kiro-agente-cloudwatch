"""Shared fixtures for self_healing_agent tests."""
import sys
from pathlib import Path

# Add the project root to sys.path so imports work from the test directory
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
