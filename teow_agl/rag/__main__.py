"""
RAG indexer CLI.

Usage:
    python -m teow_agl.rag                           # index profile.workspace_roots
    python -m teow_agl.rag --root ./workspace --root ./outputs
    python -m teow_agl.rag --profile default_user_governance_profile.json

Writes the index to state/rag/index.jsonl by default.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config_loader import load_config
from ..policies.governance_profile import ProfileView
from .indexer import build_index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="teow_agl.rag")
    parser.add_argument("--config-dir", default="configs")
    parser.add_argument("--profile", default="default_user_governance_profile.json")
    parser.add_argument("--root", action="append", default=[],
                        help="Override roots; may be passed multiple times.")
    parser.add_argument("--out", default="state/rag/index.jsonl")
    parser.add_argument("--chunk-target", type=int, default=700)
    parser.add_argument("--chunk-overlap", type=int, default=80)
    args = parser.parse_args(argv)

    cfg = load_config(args.config_dir, profile_filename=Path(args.profile).name)
    profile = ProfileView(cfg.governance_profile)
    roots = args.root or profile.workspace_roots
    if not roots:
        print("ERROR: no workspace roots configured.", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    print(f"indexing roots: {roots}")
    print(f"sensitive patterns excluded: {profile.sensitive_patterns}")
    header = build_index(
        roots=roots, profile=profile, out_path=out_path,
        chunk_target=args.chunk_target, chunk_overlap=args.chunk_overlap,
    )
    print(json.dumps(header, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
