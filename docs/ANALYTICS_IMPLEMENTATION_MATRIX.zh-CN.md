# CreatorScope 分析实现矩阵（V1.2 审计）

审计基线：2026-11-21；分支 `main`；正式数据库 `instance/creatorscope_unified.sqlite3`；只读备份与哈希、完整字段覆盖见 [DATA_FIELD_COVERAGE.zh-CN.md](DATA_FIELD_COVERAGE.zh-CN.md)。

状态含义：`IMPLEMENTED`=主要流程与可用数据已具备；`PARTIAL`=已有实现但字段、过滤、图表或质量条件不完整；`MISSING`=没有对应实现；`BLOCKED`=代码已有，但真实数据不足以产出结果。状态按当前代码和本次真实数据库核定，不把合成测试或历史导入当成本轮实时采集。

## 11 项分析功能

| # | 功能 | 状态 | 优先级 | 真实字段与样本 | 代码位置 | 当前问题 / 下一步 |
|---:|---|---|---|---|---|---|
| 1 | 创作者粉丝、报价、领域统计 | PARTIAL | P0 | 粉丝值和报价在当前分析输出各 2 个有效创作者；粉丝-报价与阅读-互动散点各 2 点，相关性阈值为 3 因此未生成。3 个报价原值含“起”被当精确数。 | `creatorscope/analytics.py`：`_merge_creator_rows()`、`creator_library()`、`analytics_data()`；`templates/index.html` / `static/js/app.js`；`/api/creators`、`/api/analytics`。 | 补起价/区间/精确分类；按来源/批次筛选；完成地域、领域、内容阅读和互动中位数统计及排行榜，并显示每图有效 n。 |
| 2 | 笔记互动数据分布 | PARTIAL | P0 | 数据库 16 篇笔记、20 条 note_detail 原始记录；当前阅读和互动排行各 2 个有效样本。缺少直方图、箱线图、新增关注分析；`analytics_data` 指标图未按来源隔离。 | `creatorscope/analytics.py`：`notes_data()`、`analytics_data()`；`templates/index.html`、`static/js/app.js`；`/api/notes`。 | 完善 7 项指标的精确样本计数、分布、箱线、散点和排行；严格按 source 分组，不将空值当 0。 |
| 3 | 笔记标题、正文、话题词云 | PARTIAL | P0 | 实体表标题 14/16、正文 11/16、话题 14/16；当前 `/api/analytics` 词频输出 0 个词，原因是读取 records.fields_json 且混合字段，而正文主要在 notes.body。 | `creatorscope/analytics.py`：`analytics_data()`、`wordcloud_png()`；`/api/wordcloud.png`；`static/js/app.js`。 | 从关联的 notes 表读取标题/正文/话题，分别做 3 种词云与话题 Top 20；实现创作者、领域、笔记类型、批次、时间过滤；保留否定词。 |
| 4 | 评论词云与高频词 | IMPLEMENTED | P0 | 真实库 33 条评论、32 条一级、1 条回复，覆盖 3 篇笔记和 3 位创作者；评论词频 Top 20 有 20 项、逐笔记关键词 36 行。原文被使用，API 搜索和批次/创作者/笔记/层级筛选已存在。 | `research/r4_3_comment_analysis/comment_analysis.py`：`summarize()`、`make_wordcloud_png()`；`comments_web.py`；`templates/comments_analysis.html`；`/api/comments/*`。 | 分析主流程已具备。B 阶段需另修评论原始 ID 的 API 命名；源文件没有评论者姓名/账号/主页值，不能用哈希冒充。 |
| 5 | 评论长度和点赞分布 | PARTIAL | P0 | 长度样本 33；精确点赞 8/33；精确回复数 20/33；长度-点赞散点 n=8。已有长度直方图、点赞直方图、按笔记数量、回复层级分布。 | `research/r4_3_comment_analysis/comment_analysis.py`：`exact_integer()`、`summarize()`；`static/js/comments-analysis.js`。 | 增加长度箱线图、回复数量分布、筛选后各图 n；继续拒绝把 `10+` 转成精确整数。 |
| 6 | 创作者受众画像 | PARTIAL | P1 | 库中 8 条 fan_analysis 记录；可用的活跃/阅读/互动/下单粉丝占比均值当前各只有 1 个样本。存在少量性别、年龄段、地域/兴趣/设备原始数据，但当前没有对应分类图与兼容口径对比。 | `creatorscope/analytics.py`：`analytics_data()` 的 `fan_analysis` 汇总；`templates/index.html`、`static/js/app.js`。 | 解析分类字段；实现性别、年龄、地域 Top10、兴趣、设备及跨创作者对比；样本不足或口径不兼容时显示缺失，不造比例。 |
| 7 | 创作者与笔记表现对比 | PARTIAL | P1 | 创作者 API 允许同批次选择 2–4 位，主界面仅返回数据表；未找到多笔记对比流程、雷达图或完整对比数据表。实体由 creator_id/note_id 关联。 | `creatorscope/__init__.py`：`/api/creators/compare`；`creatorscope/analytics.py`：`creator_library()`；`templates/index.html`、`static/js/app.js`。 | 补多创作者/多笔记同口径比较、柱状/雷达/明细表；只使用精确实体关联，继续拒绝昵称/标题自动合并。 |
| 8 | 评论情感和主题分析 | MISSING | P1 | 当前实现分词词频与主题词展示；没有传统情感词典、否定词/程度副词规则、TF-IDF 分类、人工抽样验证。词频不能代表情绪。 | `research/r4_3_comment_analysis/comment_analysis.py` 只有 `tokenize()`、词频与批次对比；未发现 sentiment/TF-IDF 模块或 API。 | 先实现可复核的规则/词典探索和人工样本表；标注小样本局限，不能把高频词直接说成情绪。 |
| 9 | 同一笔记跨批次趋势 | BLOCKED | P1 | 已有趋势代码按 note_id、source、metric_key、period、captured_at 分组并要求至少两个时间点；正式库当前符合条件的重复笔记指标序列为 0，创作者趋势组也为 0。 | `creatorscope/analytics.py`：`notes_data()` 的 `metric_trends` 与 `analytics_data()` 的 `batch_trend`；`static/js/app.js`。 | 代码需维持精确关联和来源/周期隔离；扩大真实重复采集后再展示曲线，当前显示缺少快照。 |
| 10 | AI 自动分析报告 | PARTIAL | P2 | 代码配置 7 种分析类型；本地原始数据预览及“确认后外发”门控已实现。当前无 API Key、无模型配置，未发生真实调用；结果通过 API 返回，未持久化为报告。 | `creatorscope/ai.py`：`build_preview()`、`run_analysis()`；`creatorscope/__init__.py`：`/api/ai/preview`、`/api/ai/analyze`；`templates/ai_analysis.html`。 | 依要求先完成本地预览与 Mock/结构验证；保留原始名称、正文、评论，不脱敏；绝不宣称真实模型分析完成。 |
| 11 | 新闻热点与笔记关联 | MISSING | P2 | 未发现新闻表、RSS/API 采集模块、新闻路由或笔记话题时间关联计算。 | 当前没有对应 Python 模块、schema 表或 `/api/news/*` 路由。 | 优先使用公开 RSS/官方 API；保留来源和发布时间，仅报告时间相关，不宣称因果。 |

## 当前筛选与联动范围

- 主看板的 `/api/analytics` 与 `/api/wordcloud.png` 当前以批次为筛选参数；没有统一的来源、创作者、笔记、领域、笔记类型、时间和关键词状态共享机制。
- 创作者库 `/api/creators` 支持关键词、类目、粉丝/报价范围、地域、排序、批次；笔记 `/api/notes` 支持批次、创作者、发布时间区间、笔记类型、话题；评论接口支持批次、创作者、笔记、一级/回复、置顶、关键词。
- 所有筛选要成为单一联动状态，并按各图表的字段口径和来源范围过滤；未提供的字段继续显示缺失，不补零。

## 真实数据边界

- 数据库共有 17 个 creator 实体，但当前分析通过原始记录关联到 15 个；有效粉丝/报价样本各 2，散点图各 2 点。
- 16 篇 notes 中正文 11 篇、标题和话题各 14 篇；当前笔记词频函数仍返回 0 个词，说明“字段存在”并不等于“分析已使用”。
- 评论 33 条全部为本地库实际保存的文本；其中 29 条源 payload 保留原始评论本地 ID，33 条来源均没有评论者姓名、账号 ID 或主页字段。评论样本只代表已保存范围。
- 所有历史导入、真实可见采集、合成测试、Mock AI 和真实模型调用必须分开标记。当前仅 1 条本轮可见蒲公英列表记录；之前的 0 记录/4 错误采集批次保持失败事实。

## 建议执行顺序与阶段提交

| 阶段 | 工作范围 | 完成状态 | 目标提交信息 |
|---|---|---|---|
| A | 数据库/代码审计、逐字段覆盖率、11 项实现矩阵 | 本文档及字段报告为审计交付 | `docs: audit current data field coverage` |
| B | 原始值、来源、采集时间完整保存；修复原始身份展示/导出 | 待开发与定向测试 | `feat: preserve complete source data and raw values` |
| C | P0 五项统计和统一筛选联动 | 待开发与定向测试 | `feat: complete P0 statistical analytics` |
| D | 受众、内容比较、主题情感、趋势 | 待开发；趋势等待重复快照 | `feat: add audience content and trend analytics` |
| E/F | 本地 AI 预览/Mock 结构、新闻时间关联 | 待开发；无真实模型调用 | `feat: implement AI analytics reports` / `feat: add news and content trend correlation` |
| G/H | 受限页面内扩充真实样本、实际数据图与课程报告/指南 | 依采集页面可见状态和前置阶段推进 | 单独按阶段提交 |
