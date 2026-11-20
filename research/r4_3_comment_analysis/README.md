# CreatorScope R4.3 评论数据分析实验

本目录从 `research/r4_2_comment_validation/raw_comments.jsonl` 导入 29 条真实页面样本，使用隔离数据库 `validation.sqlite3`。不会初始化或覆盖 `instance/creatorscope.sqlite3`。

评论正文按 R4.2 原始字段保留，不脱敏、不改写；`cleaned_text` 单独保存，只折叠空白。R4.2 没有评论者身份字段，因此本轮不会补采或推断评论者身份。创作者名称与笔记标题来自 R4.2 实测质量记录的明确映射，原样保留。

## 本地运行

在项目根目录执行：

```powershell
\.venv\Scripts\python.exe research\r4_3_comment_analysis\import_r42_comments.py
\.venv\Scripts\python.exe research\r4_3_comment_analysis\import_r42_comments.py
\.venv\Scripts\python.exe research\r4_3_comment_analysis\analyze_comments.py
\.venv\Scripts\python.exe research\r4_3_comment_analysis\validate_r4_3.py
\.venv\Scripts\python.exe research\r4_3_comment_analysis\serve_comments.py
```

第二次导入同一个 R4.2 批次应新增 0 条、跳过 29 条已存在记录。页面地址为 `http://127.0.0.1:8766/comments`。这个运行入口通过 Flask 工厂显式指向本目录验证数据库；主库不参与写入。

CreatorScope 主应用已增加 `/comments` 路由入口，并通过 `COMMENT_ANALYSIS_DATABASE_PATH` 与通用 `DATABASE_PATH` 分开；默认评论数据源指向本目录验证库，也可用环境变量 `CREATORSCOPE_COMMENT_DB` 指定。评论页的创作者筛选和明细使用 R4.2 实测的真实显示名称，不替换成匿名编号。

本地服务启动后，可用另一个终端运行 `\.venv\Scripts\python.exe research\r4_3_comment_analysis\browser_acceptance.py` 完成可见浏览器交互验收并保存截图。

## 数据结构

- `collection_batches`：来源批次。
- `comment_creators`：本次样本能可靠关联到的创作者及真实显示名称。
- `comment_notes`：笔记 ID、标题和创作者外键。
- `comment_snapshots`：按批次追加的评论快照，保留原文、派生清洗文本、原始互动显示值和完整源行。

`local_comment_id` 只作为同批次指纹，不是平台稳定评论 ID；不用于跨批次覆盖或匹配主体。回复以同批次、同笔记的 `parent_comment_id` 外键关联一级评论。

## 输出

- `exports/comments_export.jsonl`、`exports/comments_export.csv`：真实评论导出。
- `exports/word_frequency.csv`：jieba 词频。
- `charts/`：中文词云、Top 20、笔记评论数量、长度、点赞、笔记关键词对比、长度与点赞散点图及参数记录。
- `reports/`：导入历史、导入报告、分析报告、质量报告和本地运行参数。
- `screenshots/`：本地网页验收截图。

点赞数只把纯数字原始值转换为整数；`10+` 和操作标签不转换，缺失值不填 0。词云停用词可编辑 `stopwords.txt`；否定词保留。数据量为 29 条，只做描述性课程实验，不做总体或情感结论。
