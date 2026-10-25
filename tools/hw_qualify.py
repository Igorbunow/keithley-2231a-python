"""Explicit qualification CLI; defaults to dry run without serial access.

@file hw_qualify.py
@author Project contributors
@date 2026-10-25
@version 0.2.0
@brief Entry point for manually gated bench qualification.
@details Scenarios live in tests/hardware and are never run by CI discovery.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests/hardware'))
from qualification import main

if __name__ == '__main__':
    raise SystemExit(main())
