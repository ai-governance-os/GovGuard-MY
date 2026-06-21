"""Restore state/ + traces/ from a backup archive.

Usage:
    python -X utf8 scripts/restore_state.py <backup.zip> [--with-configs]

Stop the server first. Files in the archive overwrite current ones;
files created after the backup are left in place. configs/ is only
restored with the explicit --with-configs flag (restoring governance
policy is an operator decision).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from teow_agl.util.backup import restore_backup  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Restore TEOW-AGL state")
    ap.add_argument("archive", help="path to teow_backup_*.zip")
    ap.add_argument("--with-configs", action="store_true",
                    help="also restore configs/ (governance policy)")
    args = ap.parse_args()

    items = ("state", "traces", "configs") if args.with_configs \
        else ("state", "traces")
    restored = restore_backup(args.archive, ROOT, items=items)
    if not restored:
        print("nothing restored — wrong archive or empty whitelist match")
        return 1
    print(f"restored {len(restored)} files from {args.archive}")
    for name in restored[:10]:
        print(f"  {name}")
    if len(restored) > 10:
        print(f"  ... and {len(restored) - 10} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
