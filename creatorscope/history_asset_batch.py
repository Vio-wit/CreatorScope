"""Import curated local rednote history assets into a dedicated evidence DB."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .history_evidence import import_file, quality_report


@dataclass(frozen=True)
class Asset:
    path: Path
    source_name: str
    identity_index_only: bool = False


def _existing(path: Path, source_name: str, *, index_only: bool = False) -> Asset | None:
    return Asset(path, source_name, index_only) if path.is_file() else None


def discover_assets(root: Path) -> list[Asset]:
    """Return a deterministic, explicit allowlist of public creator/note assets."""
    root = root.expanduser().resolve(strict=True)
    assets: list[Asset] = []

    direct = (
        ("02_MASTER_CREATOR_INDEX_REBUILD_20260820.csv", "rednote_creator__重建索引", False),
        ("MASTER_CREATOR_INDEX_20260820.csv", "rednote_creator__旧版索引", False),
        ("09_IDENTITY_DEDUP_CORRECTED_20260820.csv", "rednote_reference__身份修正", True),
        ("01_DATA_LINEAGE_20260820.csv", "rednote_reference__数据血缘", True),
        ("000001_extracted/output/蒲公英博主完整数据_run1_balanced.csv", "rednote_creator__早期详情", False),
        ("influencer-ai/backend/datasets/output/蒲公英博主完整数据.csv", "rednote_creator__详情汇总", False),
        ("outputs/MUSINSA_STANDARD_candidate_pool_20260904/蒲公英候选池_原始列表字段.csv", "rednote_creator__MUSINSA", False),
        ("outputs/MUSINSA_STANDARD_candidate_pool_20260904/蒲公英候选池_采集成功记录.csv", "rednote_creator__MUSINSA", False),
        ("outputs/ept_creator_expansion_pgy_20260914/EPT_蒲公英_韩国达人完整候选池_20260914.csv", "rednote_creator__EPT", False),
        ("000001_extracted/output/notes/seongsu_20260808/notes.csv", "rednote_note__结构化笔记", False),
        ("000001_extracted/output/notes/korea_fashion_7d_20260913_韩国/notes.csv", "rednote_note__结构化笔记", False),
    )
    for relative, label, index_only in direct:
        item = _existing(root / relative, label, index_only=index_only)
        if item:
            assets.append(item)

    blogger_root = root / "influencer-ai/backend/datasets/output/bloggers"
    if blogger_root.is_dir():
        for path in sorted(blogger_root.rglob("record.json")):
            assets.append(Asset(path, "rednote_creator__详情JSON"))
        # Notes are deliberately scoped to notes folders. Other TXT files in
        # blogger records are profile/detail dumps, not note bodies.
        for path in sorted(blogger_root.rglob("*.txt")):
            if path.parent.name.casefold() == "notes":
                assets.append(Asset(path, "rednote_note__历史笔记文本"))

    outputs = root / "outputs"
    if outputs.is_dir():
        for directory in sorted(outputs.iterdir()):
            if not directory.is_dir():
                continue
            folded = directory.name.casefold()
            if "ept" not in folded and "musinsa" not in folded:
                continue
            for path in sorted(directory.rglob("*.csv")):
                if path.name == "蒲公英候选池_原始列表字段.csv":
                    continue  # already included above
                label = "rednote_creator__MUSINSA补充候选" if "musinsa" in folded else "rednote_creator__EPT补充候选"
                assets.append(Asset(path, label))

        # These are separate ranking/export snapshots, useful as source evidence.
        for path in sorted(outputs.glob("*.csv")):
            if any(token in path.name for token in ("候选池", "TOP20", "蒲公英")):
                assets.append(Asset(path, "rednote_creator__候选名单"))

    # Keep deterministic order while avoiding the same physical path twice.
    seen: set[Path] = set()
    result: list[Asset] = []
    for asset in assets:
        canonical = asset.path.resolve()
        if canonical not in seen:
            seen.add(canonical)
            result.append(Asset(canonical, asset.source_name, asset.identity_index_only))
    return result


def _summary(assets: Iterable[Asset]) -> dict[str, object]:
    items = list(assets)
    by_kind = Counter(asset.source_name for asset in items)
    total_bytes = sum(asset.path.stat().st_size for asset in items)
    return {
        "asset_files": len(items),
        "asset_bytes": total_bytes,
        "by_source_group": dict(sorted(by_kind.items())),
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(db_path: Path, root: Path, report_path: Path, *, dry_run: bool = False) -> dict[str, object]:
    project_root = Path(__file__).resolve().parents[1]
    preview = (project_root / "_preview").resolve()
    db_path = db_path.expanduser().resolve()
    try:
        db_path.relative_to(preview)
    except ValueError as exc:
        raise ValueError("历史导入数据库必须位于项目 _preview 目录内") from exc
    if db_path == report_path.expanduser().resolve():
        raise ValueError("数据库和质量报告不能使用同一路径")

    assets = discover_assets(root)
    result: dict[str, object] = {
        "database": str(db_path),
        "input_root": str(root.expanduser().resolve()),
        "dry_run": dry_run,
        **_summary(assets),
    }
    if dry_run:
        return result

    if db_path.exists():
        # import_file will allow only an existing, marked evidence DB. Refuse
        # early if the marker is absent; it never writes the formal app DB.
        from .history_evidence import _open_database  # local safety check, then close

        conn = _open_database(db_path)
        conn.close()

    imports: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    for index, asset in enumerate(assets, 1):
        try:
            outcome = import_file(
                db_path,
                asset.path,
                asset.source_name,
                identity_index_only=asset.identity_index_only,
            )
            imports.append({"path": str(asset.path), **outcome})
        except Exception as exc:  # continue so one malformed asset does not block the rest
            failures.append({"path": str(asset.path), "error": f"{type(exc).__name__}: {exc}"})
        if index % 250 == 0:
            print(f"已处理 {index}/{len(assets)} 个文件", file=sys.stderr, flush=True)

    quality = quality_report(db_path) if db_path.is_file() else None
    result.update({
        "imports": imports,
        "failures": failures,
        "completed_files": sum(item.get("status") == "completed" for item in imports),
        "duplicate_files": sum(item.get("status") == "duplicate" for item in imports),
        "stored_source_records": sum(int(item.get("records", 0)) for item in imports if item.get("status") == "completed"),
        "parsed_rows": sum(int(item.get("parsed", 0)) for item in imports if item.get("status") == "completed"),
        "row_errors": sum(int(item.get("errors", 0)) for item in imports if item.get("status") == "completed"),
        "quality": quality,
    })
    _write_json(report_path, result)
    return {key: value for key, value in result.items() if key not in {"imports", "quality"}}


def main() -> None:
    parser = argparse.ArgumentParser(description="将本地 rednote 历史资料导入隔离历史证据库")
    parser.add_argument("--db", type=Path, required=True, help="位于 CreatorScope/_preview 内的隔离数据库路径")
    parser.add_argument("--root", type=Path, required=True, help="redbookdate 本地资料目录")
    parser.add_argument("--report", type=Path, help="导入结果 JSON 路径，默认与数据库同目录")
    parser.add_argument("--dry-run", action="store_true", help="仅盘点将导入的文件，不写库")
    args = parser.parse_args()
    report = args.report or args.db.with_suffix(".quality_report.json")
    try:
        result = run(args.db, args.root, report, dry_run=args.dry_run)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.dry_run:
        print(f"完整字段覆盖与来源报告：{report}")


if __name__ == "__main__":
    main()
