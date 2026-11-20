from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from creatorscope import create_app


DEFAULT_DB = PROJECT_ROOT / "instance" / "creatorscope_unified.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the CreatorScope comment page against the unified local database.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    app = create_app({"DATABASE_PATH": args.db.resolve()})
    print(f"CreatorScope comment dashboard: http://{args.host}:{args.port}/comments")
    print(f"Unified database: {args.db.resolve()}")
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
