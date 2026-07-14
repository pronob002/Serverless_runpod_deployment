"""
CLI entry point for the Module 2 batch runner.

Run once per model, whenever you want — that is how the same voice gets cloned by different models
"at different times" for a later side-by-side listen.

    python -m module2.run --list-models
    python -m module2.run --list-sessions
    python -m module2.run --session <session_id> --model voxcpm

Run it from the `live_demo_of_voice_capture/` directory (so `protocol` and `module2` import cleanly).
"""

import os
import sys
import argparse

from . import registry
from . import reference
from .runner import run_model

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_ROOT = os.path.join(os.path.dirname(HERE), "output")  # live_demo_of_voice_capture/output


def _list_sessions() -> list[str]:
    """Session ids under output/ that have at least the calm baseline clip Module 2 needs."""
    if not os.path.isdir(OUTPUT_ROOT):
        return []
    sessions = []
    for name in sorted(os.listdir(OUTPUT_ROOT)):
        session_dir = os.path.join(OUTPUT_ROOT, name)
        if os.path.isdir(session_dir) and reference.has_anchors(session_dir):
            sessions.append(name)
    return sessions


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m module2.run",
        description="Batch voice-cloning runner for Module 1 capture sessions.",
    )
    parser.add_argument("--session", help="Module 1 session id (a folder name under output/).")
    parser.add_argument("--model", help="Cloning model to run (see --list-models).")
    parser.add_argument("--list-models", action="store_true", help="List registered models and exit.")
    parser.add_argument("--list-sessions", action="store_true",
                        help="List sessions that have clips ready for cloning, and exit.")
    args = parser.parse_args(argv)

    if args.list_models:
        print("Available models:")
        for name in registry.available():
            print(f"  {name}")
        return 0

    if args.list_sessions:
        sessions = _list_sessions()
        if not sessions:
            print(f"No sessions with anchors found under {OUTPUT_ROOT}.")
            return 0
        print("Sessions ready for cloning:")
        for name in sessions:
            print(f"  {name}")
        return 0

    if not args.session or not args.model:
        parser.error("--session and --model are required (or use --list-models / --list-sessions).")

    if args.model not in registry.available():
        parser.error(f"Unknown model '{args.model}'. Available: {', '.join(registry.available())}.")

    session_dir = os.path.join(OUTPUT_ROOT, args.session)
    if not os.path.isdir(session_dir):
        parser.error(f"Session folder not found: {session_dir}")

    run_model(session_dir, args.model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
