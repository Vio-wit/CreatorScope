# CreatorScope 接力开发交接说明

打包时间：2026-11-25
默认分支：main（已合并 v2.6-frontend-visual 全部成果并推送远程）
最新提交：074c2981c2740dfd85816e029c2fd64d245904e9（main 合并提交）
特性分支：v2.6-frontend-visual（207bacc，保留备查）
远程仓库：git@github.com:Vio-wit/CreatorScope.git（SSH）
作者：Vio-wit <3264049343@qq.com>

## 一、这个包里面有什么

- 应用源码：creatorscope/、static/、templates/、scripts/、tests/、app.py
- 各阶段采集与分析脚本、报告：research/、docs/
- 运行数据库与采集原始资料：instance/、data/、_preview/、_backup/、output/
- 前端离线依赖：vendor_sources/（ECharts 6.1.0 发行文件与 tgz，页面不依赖远程脚本）
- 版本库目录：.git/（保留提交历史与远程跟踪，解压后可直接继续 commit / push）
- 配置与脚本：requirements.txt、setup.ps1、run.ps1、README.md、.gitignore

## 二、包里没有什么（需要在新机重建）

- `.venv/`：Python 虚拟环境已排除，按下面步骤重建。
- `__pycache__/`、`*.pyc`：缓存，自动再生。
- 浏览器登录态：隔离浏览器配置目录在 `%LOCALAPPDATA%\CreatorScope\pgy_browser_profile`，不在包内；新机首次打开浏览器入口需要重新登录蒲公英/小红书。账号凭据不要写入项目文件。
- Playwright Chromium 二进制：setup.ps1 检测缺失后自动 `playwright install chromium`。

## 三、新机还原步骤

1. 安装 Python 3.11（setup.ps1 通过 `py -3.11` 创建环境）。
2. 解压本包到目标目录（路径避免含特殊字符），例如 `K:\work\CreatorScope`。
3. 在项目根目录执行：

```powershell
cd <项目根目录>
.\setup.ps1      # 建 .venv、按 requirements.txt 装依赖、补装 Chromium
.\run.ps1        # 启动本地服务
```

4. 浏览器打开 <http://127.0.0.1:8765>，只绑定回环地址。停止在运行窗口按 `Ctrl+C`。

依赖固定版本见 requirements.txt：Flask 3.1.3、playwright 1.60.0、pandas 2.3.3、numpy 2.4.6、jieba 0.42.1、WordCloud 1.9.6、matplotlib 3.11.2、python-docx 1.1.2。

## 四、数据库与资源清单

### instance/（当前运行库，应用直接读取）

- `creatorscope_unified.sqlite3`：统一主库（批次 / 达人 / 笔记 / 评论 / 记录 / 指标）。
- `creatorscope_collect_demo.sqlite3`：中等样本演示库，含一个失败批次 30be0993（用于失败原因排查样例）。
- `creatorscope_collect_demo_short_20261124.sqlite3`：短样本提速验收库。
- `creatorscope.sqlite3`：旧主库（迁移前留底）。
- `news_items.jsonl`：资讯条目原始快照。
- 各库伴随的 `-shm` / `-wal` 文件请与主库一起保留，不要单独删除。

### _preview/（历史证据库与课堂演示库，不被应用默认加载）

- `v24_history_expanded_final_20261123.sqlite3`：历史证据库终版（sources / source_records / identities / field_evidence，资料时间 2026-08-08 ~ 09-14）。
- `v24_history_expanded_20261123.sqlite3`：历史库同版副本。
- `v22_s3_history*.sqlite3`（含 final / restore_check）：上一版历史库。
- `v2_field_validation_20261123.sqlite3`：课堂字段校验库。
- `creatorscope_stage_f_demo_20261121.sqlite3`、`v24_ui_demo.sqlite3`：阶段演示库。
- 同目录的 `*.quality_report.json` 与风控/登录截图为对应库的验收留档。

### _backup/

迁移前的旧库快照集合，仅作回滚留底，应用不读取；确认无需回滚后可手动删除以节省空间。

### data/

- `baseline/pgy_live_sample_2026-11-19.json`：匿名基线真实样本（应用“导入基线样本”读取）。
- `demo/creatorscope_demo.sqlite3`：演示数据。

### research/

按阶段分目录：探针脚本、解析脚本、可见文本原始快照、运行日志、质量/采集报告、分析产物（charts / exports / screenshots）。报告类 `.md` 与 `quality_report.json` 已纳入 Git；原始 `.txt` / `.jsonl` / `.csv` / `.sqlite3` / 截图为采集留档，随本包携带、不进版本库。

### output/

看板与分析页的截图验收产物。

## 五、Git 接力

- 本包携带 `.git/`，解压后直接 `git status` 即可看到完整历史。
- 推送走 SSH：新机需把本机 `~/.ssh/id_ed25519.pub` 对应的公钥添加到 GitHub 账号 Vio-wit；若新机是另一把密钥，需在 GitHub 重新登记。
- 远程含 `main`（最新，已合并全部成果）与备查特性分支 `v2.6-frontend-visual`；接力开发从 `main` 开始。
- `.gitignore` 已排除 `.venv`、历史库、备份库、原始快照与分析产物；新增数据文件默认不进 Git，需要入库时手动 `git add` 并说明理由。

## 六、已知遗留（接续开发时先看）

- 散点/趋势类图表偏空，多为样本口径限制：报价多为“起”价文案未归一为数值；缺同一创作者跨时间重复快照；发布时间含“今天 11:01”这类相对时间；unified 近期记录为 0 是基线时间之后没有新采集。
- 旧库遗留脏值：平台 pgy / xiaohongshu / xhs 混用、笔记类型 video / 视频 / 空混用、属地格式不齐、同名达人未强合并、领域类目停留在原始显示文本、评论点赞纯数字约占四分之一。归一规则代码侧已落地，但旧库未回溯重采。
- 蒲公英列表/详情解析面向可见 DOM 与可访问性树；筛选、分页遍历、批量详情跳转与笔记列表循环暂未处理。
