# CreatorScope 分析实现矩阵（V1.2 收口）

审计日期：2026-11-21。项目分支：main。正式 SQLite 数据库：instance/creatorscope_unified.sqlite3。数据库字段逐项计数、来源和批次覆盖详见 DATA_FIELD_COVERAGE.zh-CN.md；实际样本图见 assets/v1.2/。

状态说明：IMPLEMENTED 表示相应分析流程已在应用中实现，不代表样本具有总体代表性；PARTIAL 表示主流程可用但数据或人工验证仍有限；BLOCKED 表示代码已实现但正式样本不满足输出条件；MISSING 表示没有实现。表中数据均来自当前正式库只读审计副本或本地 RSS JSONL，合成测试和 Mock AI 单独标注。

## 11 项分析能力

| # | 功能 | 状态 | 优先级 | 实际字段与样本 | 代码与页面 | 限制 / 后续 |
|---:|---|---|---|---|---|---|
| 1 | 创作者粉丝、报价、领域统计 | PARTIAL | P0 | 17 位实体；原始公开名称 14/17、平台 ID 5/17、类目与地域各 6/17。当前可参与粉丝和报价联合分析的创作者各 n=2。 | creatorscope/analytics.py 的 creator_library、analytics_data；/api/creators、/api/analytics；首页创作者与分析区。 | 分布、报价类型和比较代码已具备；n=2 只能作描述统计，旧库还有 3 个不可恢复的占位字段。 |
| 2 | 笔记互动分布 | IMPLEMENTED | P0 | 16 篇笔记、20 条 note_detail 记录；各阅读、曝光、点赞、收藏、评论、分享、关注图按来源及可用数值分别显示 n。 | creatorscope/analytics.py 的 notes_data、analytics_data；/api/notes；首页笔记分析区。 | 缺失或非精确原值不作 0；各来源数据口径不同，不能合并解释。 |
| 3 | 标题、正文和话题分析 | IMPLEMENTED | P0 | 标题 14/16、正文 11/16、话题字段 14/16，共 125 个原始话题标签；词云按实际文本字段分别生成。 | creatorscope/analytics.py 的 notes_data、wordcloud_png；/api/wordcloud.png；首页笔记分析区。 | 空正文不会用标题替代；当前样本较小，词频不代表平台整体内容。 |
| 4 | 评论词云与高频词 | IMPLEMENTED | P0 | 33 条原文评论，32 条一级、1 条回复；覆盖已保存笔记范围。 | research/r4_3_comment_analysis/comment_analysis.py、comments_web.py；/comments 与 /api/comments/*。 | 仅代表已保存的评论，不代表评论区全部内容。 |
| 5 | 评论长度、点赞和回复分布 | IMPLEMENTED | P0 | 原文长度 n=33；精确点赞 n=8；26 条有回复数原始显示值，其中精确数值按规范解析，非精确值排除。 | research/r4_3_comment_analysis/comment_analysis.py；static/js/comments-analysis.js。 | 直方图、箱线图、精确点赞直方图、长度-点赞散点、回复分布均展示有效 n；不把“10+”当作 10。 |
| 6 | 创作者受众画像 | PARTIAL | P1 | 8 条 fan_analysis；性别、年龄、地域、兴趣、设备及互动相关原始值分布于不同来源与周期，完整兼容组有限。 | creatorscope/analytics.py 的 audience_groups；首页受众画像区。 | 按来源与周期分组展示，只有来源提供的分类值才绘图；不从“领先分类”推造百分比。 |
| 7 | 创作者和笔记表现对比 | IMPLEMENTED | P1 | 创作者比较限 2–4 位且指定同一批次；笔记按精确 note_id 比较，并保持来源和指标周期口径。 | /api/creators/compare、/api/notes/compare；creatorscope/analytics.py、static/js/app.js。 | 不依据昵称或相似标题自动合并实体；样本可用性由每项指标的 n 决定。 |
| 8 | 评论情感与主题探索 | PARTIAL | P1 | 真实评论 n=33：词典倾向 12 正向、20 中性/未命中、1 负向；TF-IDF 对 33 条评论计算并汇总关键词；提供确定性人工复核样本 CSV，最多 20 条。 | research/r4_3_comment_analysis/sentiment.py、comments_web.py；/api/comments/sentiment、人工复核 CSV、评论页。 | 规则和 TF-IDF 都是探索线索，没有人工标注金标准，不能代表整体情绪或推断个体情绪。 |
| 9 | 同一笔记跨批次趋势 | BLOCKED | P1 | 趋势要求 note_id、source、metric_key、period 一致且至少两个采集时间点；当前符合条件的笔记指标序列 n=0。 | creatorscope/analytics.py 的 metric_trends、batch_trend；笔记分析区。 | 页面显示缺少快照；需在获准访问范围内对同一笔记进行后续重复采集。 |
| 10 | AI 分析报告 | PARTIAL | P2 | 支持 7 类分析预览；可单独选择正文/评论原文是否进入模型输入（默认关闭）；检查预览并明确确认后才调用模型；成功结果本地保存模型、批次、实体 ID、事实和输出。当前实际模型调用 n=0。 | creatorscope/ai.py；/api/ai/preview、/api/ai/analyze、/api/ai/reports；/ai。 | 仅检测到 DeepSeek 环境变量存在，所有服务商均未配置模型 ID；测试使用 Mock 响应，只验证结构和本地持久化，不代表真实 AI 分析。 |
| 11 | 新闻热点与笔记关联 | PARTIAL | P2 | 从新华网时政 RSS 保存 300 条新闻；RSS 项目没有 pubDate，全部 300 条时间由文章 URL 日期路径推得，覆盖 2022-12-08 至 2022-12-14。 | creatorscope/news.py；/news、/api/news/*；instance/news_items.jsonl。 | 是历史归档；按关键词、来源、周期和日期窗口作描述关联，不代表新闻导致笔记变化。来源：[新华网 RSS 目录](https://www.news.cn/linktous.htm)。 |

## 原始信息规则与样本边界

- 创作者公开名称、平台 ID、笔记标题/正文/话题、评论原文、页面原始指标均按采集值保存和展示；清洗结果另存，不覆盖原文。只有 Cookie、Session、访问 Token、密码等认证秘密被排除。
- 旧字段 pseudonym 仅用于数据库兼容和内部唯一性；展示逻辑优先返回原始公开名称，其次平台 ID。两者都缺失时明确显示“历史样本未保存原始名称”。正式库中 3 个早期占位值的来源没有可恢复原名，不反推、不编造。
- 已有历史导入、之前页面采集、当前批次、合成测试和新闻 RSS 分开统计。V1.2 本轮没有新增社交平台创作者、笔记或评论；当前页面采集器未连接到可用登录页面，因此没有伪造样本或绕过访问限制。
- 小样本图表只用于描述本地已保存数据。跨来源、跨周期或非精确值不混算；缺失不填 0。
- 正式数据库只读审计哈希：SHA-256 4F044C4C7E2205EEA402AEF98AE97B3C5678EDCD55D5D165551B22839231654A。只读副本完整性检查为 ok，外键违规数 0。本轮没有修改正式数据库。

## 阶段交付

| 阶段 | 结果 | 提交 |
|---|---|---|
| A | 数据库审计、字段覆盖率和初始矩阵 | ed8ed10 |
| B | 原始数据保存和导出规则 | 2c6862a |
| C | P0 统计分析 | 45aaa72 |
| D | 受众、比较和趋势分析 | 74d1388 |
| D 补充 | 评论 TF-IDF 关键词分析 | a62cf7b |
| E | AI 本地报告和历史记录 | ccd8186 |
| F | 官方 RSS 新闻采集与描述性关联 | f5e33f5 |
| G | 社交平台数据扩充尝试与阻塞边界记录；新增创作者/笔记/评论均为 0 | 未产生代码提交；未启动新采集批次 |
| H | 真实数据图表、复现脚本、课程报告与使用指南 | 本提交 |
