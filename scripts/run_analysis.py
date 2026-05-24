#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.ev import compute_ev, load_rows, save_ev
from report.note import generate_note_markdown, write_note
from strategy.betting import generate_tickets


def main() -> None:
    in_csv = Path("data/processed/race_last5.csv")
    ev_csv = Path("data/processed/race_ev.csv")
    note_md = Path("report/note.md")

    rows = load_rows(in_csv)
    scored = compute_ev(rows)
    save_ev(scored, ev_csv)

    tickets_safe = generate_tickets(scored, mode="safe")
    note = generate_note_markdown("JRAレース", scored, tickets_safe)
    artifact_md = write_note(note_md, note)

    print(f"EV rows: {len(scored)}")
    print(f"CSV: {ev_csv}")
    print(f"note: {note_md}")
    print(f"artifact: {artifact_md}")


if __name__ == "__main__":
    main()
