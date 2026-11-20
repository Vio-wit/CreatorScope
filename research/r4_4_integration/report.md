# CreatorScope V1 历史数据整合审计

生成时间：2026-11-21T01:54:13+08:00
目标数据库：`creatorscope_unified.sqlite3`（本地忽略文件）

## 文件盘点

- research 下文件：277；JSONL：16；CSV：25；SQLite 主文件：2。
- 实际导入白名单：7 个原始文件；每个来源按 SHA-256 形成独立可重复导入批次。
- R4.2 原始评论：29 行，其中已存在于阶段 A 统一库 29 行；本阶段未再次导入。
- R4.3 验证库、图表、分析 CSV 和过程截图属于验证或派生材料，未作为新采集批次导入。

## 导入结果

- 解析成功：68 条顶层来源行；JSONL 格式错误：0 行。
- 新增 records：0；同批次重复跳过：81；新增指标快照：0。
- 本次执行补全历史指标周期：0 项；原始记录未覆盖。
- 新增评论快照：0；重复评论跳过：4。摘录与完整可见评论按来源范围区分。
- 当前白名单批次累计：records 81，metric_values 402（其中 262 项有统计周期），comment_snapshots 4。
- 数据库差量：creators +0，notes +0，records +0，metric_values +0，comment_snapshots +0。

## 字段缺失与关联

- 精确平台笔记 ID：9 行；无平台 ID、按文件和行隔离的笔记：8 行。
- 评论行：49；关联到笔记：49（100.0%）。
- 创作者关联只使用源记录中的平台 creator_id；缺失时生成来源隔离的匿名 creator，不按昵称或标题合并。平台 ID 只存 SHA-256。
- 档案精确 ID 覆盖：5/5；笔记直接关联 creator_id：9/17（52.9%）；评论关联笔记：49/49（100.0%）。
- 每个来源字段覆盖率详见 `report.json`；字段名和计数已脱敏，不含采集值。

## 文件级审计

| 来源文件 | 解析行 | 格式错误 | 原始重复行 | 缺失字段（低于全量） |
|---|---:|---:|---:|---|
| `research/continuous_collection_validation/raw_creators.jsonl` | 5 | 0 | 0 | image_quote_cny, profile_url |
| `research/continuous_collection_validation/raw_notes_pgy.jsonl` | 9 | 0 | 0 | note_id, note_url, published_at |
| `research/continuous_collection_validation/raw_notes_xhs.jsonl` | 1 | 0 | 0 | shares_raw |
| `research/continuous_collection_validation/raw_comments.jsonl` | 45 | 0 | 0 | — |
| `research/xhs_public_access_probe/raw_notes_xhs.jsonl` | 6 | 0 | 0 | body_text, capture_scope, note_type |
| `research/xhs_public_access_probe/raw_comments_xhs.jsonl` | 1 | 0 | 0 | likes_normalized, replies_normalized |
| `research/pgy_note_detail_investigation_20261120/actual_samples.json` | 1 | 0 | 0 | — |

## 随机抽查

固定随机种子抽取 10 个导入实体；为保护原始内容，报告仅列来源行、类型、关联状态、指标数和匿名审计令牌。

| 来源 | 行 | 类型 | Creator 关联 | Note 关联 | 结果 | 指标数 | 审计令牌 |
|---|---:|---|---|---|---|---:|---|
| `research/continuous_collection_validation/raw_comments.jsonl` | 43 | comment_excerpt_sample | linked | source_note_key | present_and_verified | 1 | `791860581ec7a57e` |
| `research/continuous_collection_validation/raw_notes_pgy.jsonl` | 8 | note_detail | linked | source_row | present_and_verified | 7 | `dec6c7854c0a8156` |
| `research/continuous_collection_validation/raw_comments.jsonl` | 11 | comment_excerpt_sample | linked | source_note_key | present_and_verified | 1 | `4e4e8dfe933ec431` |
| `research/continuous_collection_validation/raw_comments.jsonl` | 20 | comment_excerpt_sample | linked | source_note_key | present_and_verified | 1 | `eb63fd8e29b655ad` |
| `research/xhs_public_access_probe/raw_comments_xhs.jsonl` | 1 | comment_snapshot | unknown | exact_platform_id | present_and_verified | 0 | `ea87da103c190411` |
| `research/continuous_collection_validation/raw_creators.jsonl` | 4 | creator_content_performance | linked | none | present_and_verified | 3 | `8cf22aeda9141bb7` |
| `research/continuous_collection_validation/raw_comments.jsonl` | 19 | comment_excerpt_sample | linked | source_note_key | present_and_verified | 1 | `3c6628b8774b49d7` |
| `research/continuous_collection_validation/raw_creators.jsonl` | 4 | creator_profile_sample | linked | none | present_and_verified | 24 | `17fc19368fb1911b` |
| `research/continuous_collection_validation/raw_creators.jsonl` | 1 | creator_profile_sample | linked | none | present_and_verified | 24 | `73d8888324b42a13` |
| `research/continuous_collection_validation/raw_notes_pgy.jsonl` | 9 | note_detail | linked | source_row | present_and_verified | 7 | `def075e00671fd82` |

## 数据范围说明

- 连续采集目录的 45 条评论只有文本摘录；作为 `comment_excerpt_sample` 保存，不计入完整评论快照。
- 小红书公开页评论和蒲公英详情中的样例评论使用合成行键；不保存评论者账号、昵称或主页链接。
- 统计周期及原始展示值保留在 records/metric_values；未将不同来源或不同时间的指标覆盖合并。
- 本报告不含具体创作者名称、正文内容、评论文本、平台 ID、Cookie、令牌或凭据。
