# 文件目录

## 根目录

| 路径 | 作用 |
|---|---|
| `README.md` | 产品简介、运行方法、边界和主要入口 |
| `pyproject.toml` | Python 包信息、依赖和工具配置 |
| `alembic.ini` | 数据库迁移配置 |
| `Dockerfile` | API 容器镜像 |
| `compose.yaml` | PostgreSQL 与 API 的本地部署组合 |
| `LICENSE` | 项目代码和自有文档的 MIT 许可 |
| `THIRD_PARTY_NOTICES.md` | 第三方数据来源与许可说明 |
| `.gitignore` | 本地数据库、密钥、缓存和输出排除规则 |
| `.dockerignore` | 容器构建上下文排除规则 |
| `.gitattributes` | 跨平台换行设置 |

## 产品代码

| 路径 | 作用 |
|---|---|
| `src/material_allocation_copilot/domain/` | 材料、构件、证据和规则结果模型 |
| `src/material_allocation_copilot/adapters/` | CSV、TreeTrace 和 OpenAI 适配器 |
| `src/material_allocation_copilot/rules/` | 尺寸、等级、结构评估和缺陷规则 |
| `src/material_allocation_copilot/solver/` | 候选图、最大匹配和软偏好排序 |
| `src/material_allocation_copilot/application/` | 权限、用例、Agent、保存和审批逻辑 |
| `src/material_allocation_copilot/persistence/` | ORM、Repository 和 Unit of Work |
| `src/material_allocation_copilot/api/` | FastAPI 入口、路由、身份和错误响应 |
| `src/material_allocation_copilot/mcp_server/` | 本地只读 MCP Server |
| `src/material_allocation_copilot/web/` | 三步网页向导 |
| `src/material_allocation_copilot/evals/` | 离线语义与安全评测数据和运行器 |
| `src/material_allocation_copilot/prompts/` | 版本化模型提示词 |
| `src/material_allocation_copilot/operations/` | 备份与恢复能力 |

## 支撑文件

| 路径 | 作用 |
|---|---|
| `tests/` | 单元、接口、数据库、MCP 和网页合同测试 |
| `migrations/` | Alembic 数据库迁移 |
| `sample-data/treetrace/` | 可公开使用的 TreeTrace 派生示例数据 |
| `scripts/bootstrap-dev.ps1` | 创建开发环境 |
| `scripts/start-demo.ps1` | 生成本地凭据并启动网页服务 |
| `scripts/fetch-treetrace-tables.ps1` | 下载并校验官方 TreeTrace 表格 |
| `scripts/build-treetrace-sample.py` | 从官方表格重建公开示例 CSV |
| `scripts/live-api-smoke.py` | 使用真实模型执行小范围接口检查 |
| `scripts/verify-release.ps1` | 运行统一发布验证 |
| `scripts/verify-backup-restore.py` | 检查 SQLite 备份恢复 |
| `scripts/verify-postgres-backup-restore.ps1` | 检查 PostgreSQL 备份恢复 |
| `scripts/check-pilot-health.py` | 检查部署环境健康状态 |
| `.github/workflows/ci.yml` | GitHub 自动测试与 PostgreSQL 迁移检查 |

## 产品文档

| 路径 | 作用 |
|---|---|
| `docs/user-guide.md` | 从配置到生成方案的操作步骤 |
| `docs/architecture.md` | 系统分层、数据流和权限边界 |
| `docs/data-and-evidence.md` | 材料字段、结构评估和缺陷记录 |
| `docs/product-boundaries.md` | 系统能力和明确非目标 |
| `docs/verification.md` | 测试、离线评测和部署验证方法 |
| `docs/adr/` | 关键架构决策 |
| `docs/images/` | README 使用的公开图片 |

## 不属于仓库的内容

以下内容由运行或开发环境产生，不进入 Git：

- `.venv/`、`__pycache__/`、`.pytest_cache/`、`.ruff_cache/`；
- `.local-data/` 中下载的原始数据；
- SQLite/PostgreSQL 数据、备份和运行输出；
- `.env`、API key、Bearer token 和部署 secret；
- 本地编辑器设置和聊天记录。
