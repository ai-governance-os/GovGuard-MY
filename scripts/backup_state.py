"""Create a timestamped backup of state/ + traces/ + configs/.

Usage:
    python -X utf8 scripts/backup_state.py [--keep 14] [--out DIR]

Schedule daily via Windows Task Scheduler:
    schtasks /Create /SC DAILY /ST 02:00 /TN "TEOW-AGL backup" ^
      /TR "<python> -X utf8 <repo>\\scripts\\backup_state.py"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from teow_agl.util.backup import create_backup, list_backups  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Backup TEOW-AGL state")
    ap.add_argument("--keep", type=int, default=14,
                    help="how many archives to retain (default 14)")
    ap.add_argument("--out", default=None, help="output directory")
    args = ap.parse_args()

    archive = create_backup(ROOT, out_dir=args.out, keep=args.keep)
    if archive is None:
        print("nothing to back up (no state/ traces/ configs/ found)")
        return 1
    size_kb = archive.stat().st_size / 1024
    print(f"backup written: {archive} ({size_kb:.0f} KB)")
    print(f"archives retained: {len(list_backups(ROOT, args.out))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
