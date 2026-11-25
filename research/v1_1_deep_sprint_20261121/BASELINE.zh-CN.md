# CreatorScope V1.1 阶段 A：项目与真实采集基线

审计日期：2026-11-21
项目：`K:\work\CreatorScope`
分支与 HEAD：`main` / `165bb8caec56255efe463aae45a2d43b67c66081`

## Git 与资料保护

开始审计时，已跟踪文件无修改；工作区有 16 组未跟踪的本地资料目录/文件，包括 `_backup/`、`_preview/`、Playwright 页面快照、既有研究目录、R4.3 验证 SQLite 及其 WAL/SHM、`vendor_sources/`。本阶段没有覆盖、删除或暂存这些资料。未发现项目内 `AGENTS.md`。

## 测试基线

- `.venv\Scripts\python.exe -m unittest discover -s tests -q`：19 项通过。
- `.venv\Scripts\python.exe -m pytest -q`：未运行测试；虚拟环境没有安装 `pytest`。项目现有套件可用 `unittest` 执行。

## 正式统一库只读核验

目标库：`instance\creatorscope_unified.sqlite3`，使用 SQLite URI `mode=ro` 查询，没有初始化或修改数据库。

- 完整性：`PRAGMA integrity_check = ok`
- 外键错误：0
- 创作者：16；笔记：16；批次：10；记录：91；指标值：468；评论快照：33；任务：11
- 记录类型：`blogger_list_sample` 2、`comment_excerpt_sample` 45、`creator_content_performance` 8、`creator_profile_sample` 8、`fan_analysis` 8、`note_detail` 20。
- 指标解析状态：parsed 401、unresolved 35、missing 19、not_numeric 7、text_or_date 4、displayed_unavailable 2。
- 任务类型只有历史导入、基线导入和浏览器打开记录；当前正式库中 `visible_collection` 任务为 0。

因此，库内数量反映已导入的历史/研究样本，不代表本轮应用内实时采集。V1 资料将其来源描述为匿名基线与历史人工可见页面记录；本轮报告会继续区分这些数据与后续应用任务。

## 采集链路现状

- `creatorscope/browser.py` 使用 Playwright persistent context、`headless=False`，配置目录为 `%LOCALAPPDATA%\CreatorScope\pgy_browser_profile`；目录存在，但开始审计时没有发现命令行引用该配置目录的活动浏览器进程。登录状态需在可见窗口实际确认，不读取 Cookie/Session。
- 创作者入口为蒲公英博主广场；流程按列表行打开详情、读取页面可见文本，尝试访问笔记数据卡，并通过可见“复制小红书笔记链接”按钮取链接。复制结果经域名与路径校验后，顺序打开独立小红书标签页。
- 小红书正文使用有限可见 DOM 选择器；评论解析器读取可见一级评论、回复、显示时间、点赞/回复原始值和置顶状态，并在滚动时以 note 范围的指纹去重。应用层只保留评论者匿名数据。
- `creatorscope/tasks.py` 将任务与批次先写入同一 SQLite，按创作者/笔记处理时增量写记录、评论与断点；任务状态支持排队、运行、暂停、停止、恢复、失败。服务重启会把 running/stopping 采集任务改为 paused。
- 现有 `tests/test_visible_collection.py` 覆盖合成浏览器的批次写入、幂等、评论父子关联、上限及暂停/恢复/停止；这些测试不证明真实网站 DOM、账号会话或端到端应用采集。
- 现有流程遇到单篇笔记错误时会记录失败点并暂停批次，当前实现没有逐条失败项重试队列；断点以创作者/笔记索引保存。

## 本轮边界

真实采集以应用网页创建的 `visible_collection` 任务为证据。只有在可见蒲公英窗口确认正常页面后才采集；登录失效、验证码、访问限制或正文进入登录门槛时保留失败/暂停记录，不绕过访问控制。数据库后续写入前应保留原库备份；真实记录只追加新批次，绝不覆盖旧批次或将历史人工样本标成自动采集。
