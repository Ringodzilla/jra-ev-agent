#!/usr/bin/env python3
"""Validate repository skill discovery, mode order and runtime agreement."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.skill_workflow import validate_skill_contracts

if __name__ == '__main__':
    validate_skill_contracts()
    print('Skill workflow contracts: OK')
