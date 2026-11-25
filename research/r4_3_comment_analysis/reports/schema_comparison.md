# CreatorScope 数据库隔离说明

检查时间：2026-11-21（Asia/Shanghai）。正式库只通过 SQLite `mode=ro` 打开。

## 正式库与当前源码差异

正式库 `instance/creatorscope.sqlite3` 有 `tasks`、`batches`、`records`、`metric_values` 四张业务表。正式库 `records` 列为 `record_id, batch_id, record_type, captured_at, fields_json, fields_hash`；没有 `creator_id`、`note_id`，也没有 `creators`、`notes` 或评论表。`batches` 使用 `captured_at`。

当前工作区 `creatorscope/db.py` 的源码 schema 已定义 `creators`、`notes`，并在 `records` 中增加创作者和笔记外键。初始化函数还会执行 schema 创建、列补齐及回填。因此本轮不在正式库运行初始化，也不直接迁移其结构。

## R4.3 验证库

`research/r4_3_comment_analysis/validation.sqlite3` 是独立数据库。它包含 R4.3 所需的 `collection_batches`、`comment_creators`、`comment_notes`、`comment_snapshots`，由导入器写入真实样本；Flask 评论路由使用 `COMMENT_ANALYSIS_DATABASE_PATH` 单独读取该库。通用 CreatorScope 主库路径仍由 `DATABASE_PATH` 管理。

回复通过同批次、同笔记的组合外键关联一级评论；同批幂等键为 `(batch_id, note_id, local_comment_id)`。`local_comment_id` 只用于同批次去重，不作为跨批次评论主体标识。

## 本轮写入边界

本轮导入、分析、Flask 测试和可见浏览器验收全部指向 R4.3 验证库或内存主库。正式 SQLite 没有执行初始化、迁移、导入或更新。
