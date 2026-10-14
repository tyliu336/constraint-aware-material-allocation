# 验证方法

## 一键验证

先准备完整开发环境和经过哈希校验的 TreeTrace 数据，然后运行：

```powershell
.\scripts\verify-release.ps1
```

脚本依次检查：

1. Python 依赖是否一致；
2. 自动化测试；
3. 冻结的离线 AI 评测；
4. Ruff 代码检查与格式；
5. Alembic 迁移能否重复执行；
6. 当前数据库结构是否与模型一致；
7. 备份恢复演练；
8. API 路径数量、应用版本、MCP SDK 和 PostgreSQL 驱动。

任何一步失败，脚本都会停止并返回非零退出码。

## 单独运行

```powershell
.\.venv\Scripts\python.exe -m pytest -ra
.\.venv\Scripts\python.exe -m material_allocation_copilot.evals.runner
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

## 真实模型检查

真实模型调用需要有效 key，并会产生 API 费用：

```powershell
$env:MAC_OPENAI_API_KEY = '<仅放在当前进程中的 key>'
$env:MAC_REQUIREMENT_MODEL = '<你的账号可用的模型 ID>'
.\.venv\Scripts\python.exe .\scripts\live-api-smoke.py
```

不要在 CI、截图、日志或问题单中公开 key。离线测试使用固定响应，不需要访问网络。

## PostgreSQL

GitHub Actions 会启动 PostgreSQL 16 服务并运行：

```text
alembic upgrade head
alembic check
```

Docker Compose 配置从挂载的 secret 文件读取数据库密码和 API principal，不把这些值写入镜像。
