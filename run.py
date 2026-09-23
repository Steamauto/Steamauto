"""
Project root entry point.
Usage:
    python run.py          # from project root
    python -m app          # from project root (equivalent)
"""
import sys
from pathlib import Path

# Ensure the project root is on sys.path when invoked directly
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import main

if __name__ == "__main__":
    main()
