# CreatorScope

面向《数据采集与处理综合实训》的 Windows 本地数据采集与分析应用。技术栈：Python、Flask、Playwright、SQLite、pandas、jieba、WordCloud 和本地 ECharts。

## 本地启动

需要 Python 3.11 或更高版本以及可访问 PyPI 的网络。PowerShell 中运行：

```powershell
.\setup.ps1
.\run.ps1
```

浏览器访问 <http://127.0.0.1:8765>。实时采集使用独立的可见浏览器配置；用户按平台正常登录。验证码、安全验证或访问权限提示会暂停任务，不绕过验证。常规统计和分析不需要模型 API Key。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 数据与离线演示

此公开源码快照不包含任何 SQLite 数据库、采集样本、采集运行日志或截图。应用会在本地初始化 SQLite；`run.ps1 -Mode Demo` 需要用户自行准备 `data/demo/creatorscope_demo.sqlite3`，该演示数据库不随仓库分发。基线导入也需要用户自行提供受权取得的数据文件。项目原始本地数据仍由数据持有人自行保管。

## 当前待办

自然语言需求理解和采集条件建议保持 BACKLOG。待真实采集稳定性、字段覆盖和样本规模验证完成后，再依据真实数据设计；常规采集、清洗与统计分析不依赖 AI 服务。

## 文档

- [分析功能实现矩阵](docs/ANALYTICS_IMPLEMENTATION_MATRIX.zh-CN.md)
- [分析使用指南](docs/ANALYTICS_USER_GUIDE.zh-CN.md)

仓库未附带软件许可证文件；公开可见不等于授予代码复用许可。
