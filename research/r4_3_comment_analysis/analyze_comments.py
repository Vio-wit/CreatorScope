from __future__ import annotations

import argparse
import json
from pathlib import Path

from comment_analysis import DEFAULT_DB, generate_analysis_report, generate_charts, summarize, fetch_comments


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate real-sample comment analysis charts and reports.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    rows = fetch_comments(args.db)
    analysis = summarize(rows)
    charts = generate_charts(analysis, args.output_dir / "charts")
    quality = generate_analysis_report(analysis, args.output_dir / "reports")
    result = {
        "records": len(rows),
        "charts": sorted(charts),
        "quality_report": quality,
        "analysis_report": str(args.output_dir / "reports" / "analysis_report.md"),
    }
    (args.output_dir / "reports" / "analysis_run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
