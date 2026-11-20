# CreatorScope R4.3 评论数据分析实验

本目录保存 R4.3 评论分析代码和本地验证产物。应用现在从 `instance/creatorscope_unified.sqlite3` 读取评论；R4.2 的 29 条原始样本只作为一个历史批次导入，不会把 R4.3 分析数据库计作第二批。

评论正文按 R4.2 原始字段保留，不改写；`cleaned_text` 单独保存，只折叠空白。原始采集文件、验证数据库、导出、图表和截图仅保留在本地，不提交到 Git。R4.2 没有评论者身份字段，因此不会补采或推断评论者身份。统一库使用匿名创作者编号；平台笔记和评论指纹只存哈希，便于关联与同批去重。

## 本地运行

在项目根目录执行：

```powershell
\.venv\Scripts\python.exe -m creatorscope.migrations
\.venv\Scripts\python.exe research\r4_3_comment_analysis\import_r42_comments.py
\.venv\Scripts\python.exe research\r4_3_comment_analysis\import_r42_comments.py
\.venv\Scripts\python.exe research\r4_3_comment_analysis\analyze_comments.py
\.venv\Scripts\python.exe research\r4_3_comment_analysis\validate_r4_3.py
\.venv\Scripts\python.exe research\r4_3_comment_analysis\serve_comments.py
```

重复导入同一个 R4.2 批次应新增 0 条、跳过 29 条已存在记录。独立验证库 `validation.sqlite3` 保留为迁移前校验来源，不再被应用写入。页面地址为 `http://127.0.0.1:8766/comments`。

CreatorScope 主应用和评论页共用 `DATABASE_PATH`，默认位置为 `instance/creatorscope_unified.sqlite3`；可用 `CREATORSCOPE_DB` 指定隔离测试库。评论页使用统一的创作者、笔记、批次和评论表。

本地服务启动后，可用另一个终端运行 `\.venv\Scripts\python.exe research\r4_3_comment_analysis\browser_acceptance.py` 完成可见浏览器交互验收并保存截图。

## 数据结构

- `collection_batches`：统一 `batches` 表的只读视图。
- `creators`、`notes`：与其他来源共用的匿名创作者和笔记。
- `comment_snapshots`：按来源批次追加的评论快照，保留原文、派生清洗文本、原始互动显示值和完整源行。

哈希后的评论指纹只用于同批次去重，不用于跨批次匹配主体。回复以同批次、同笔记的父评论哈希外键关联一级评论。

## 输出

- `exports/comments_export.jsonl`、`exports/comments_export.csv`：真实评论导出。
- `exports/word_frequency.csv`：jieba 词频。
- `charts/`：中文词云、Top 20、笔记评论数量、长度、点赞、笔记关键词对比、长度与点赞散点图及参数记录。
- `reports/`：导入历史、导入报告、分析报告、质量报告和本地运行参数。
- `screenshots/`：本地网页验收截图。

点赞数只把纯数字原始值转换为整数；`10+` 和操作标签不转换，缺失值不填 0。词云停用词可编辑 `stopwords.txt`；否定词保留。数据量为 29 条，只做描述性课程实验，不做总体或情感结论。
