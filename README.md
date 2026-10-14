# Constraint-Aware Material Allocation

一个面向再利用木材的约束分配原型。

回收木料不像标准化新料：尺寸、等级、缺陷和记录完整度都会不同。设计端的每根构件也有自己的长度、截面和用途要求。这个项目先用确定性规则判断材料与构件是否兼容，再用匹配算法寻找能够覆盖整个结构的方案，最后按照用户输入的偏好排列候选结果。

LLM 只把自然语言转换成受支持的排序偏好，不负责修改尺寸、等级、缺陷等硬条件。即使模型不可用，确定性校验和求解部分仍然可以独立测试。

## 页面流程

主页只有三个步骤：

1. 上传木料 CSV，或载入内置演示材料；
2. 在三维点阵中连接节点，描述简单木构框架；
3. 输入偏好，例如“优先减少余料”或“尽量选择表面节疤较少的材料”。

服务端调用配置的模型，把第三步的文字转换为结构化权重。随后，规则引擎和匹配算法会生成最多三个方案，并显示构件、分配材料、长度余量和排序得分。


## 本地运行

需要 Python 3.12 或更高版本。

```powershell
.\scripts\bootstrap-dev.ps1 -SkipTreeTrace
$secureKey = Read-Host 'OpenAI API key' -AsSecureString
$keyPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
$env:MAC_OPENAI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPtr)
$env:MAC_REQUIREMENT_MODEL = '<你的账号可用的模型 ID>'
.\scripts\start-demo.ps1
```

终端会显示本地地址和一次性 Bearer token：

1. 打开 `http://127.0.0.1:8000/`；
2. 在“本地连接”中粘贴 Bearer token；
3. 上传 [`sample-data/treetrace/treetrace-boards-demo.csv`](sample-data/treetrace/treetrace-boards-demo.csv)；
4. 连接点阵中的节点；
5. 输入偏好并生成方案。

API key 只由后端进程读取，不会发送到浏览器，也不应写入项目文件。停止服务后可以清理当前终端中的变量：

```powershell
Remove-Item Env:MAC_OPENAI_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:MAC_REQUIREMENT_MODEL -ErrorAction SilentlyContinue
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPtr)
```

完整操作见 [使用说明](docs/user-guide.md)。


## 结构

```text
网页向导 / MCP 客户端
          │
      FastAPI 接口
          │
 身份与权限 / 应用服务
          │
规则检查 / 匹配与排序 / 受控 Agent
          │
 Repository / Unit of Work
          │
 SQLite（本地）/ PostgreSQL（部署）
```

主要目录：

```text
src/material_allocation_copilot/
  domain/          领域模型和输入合同
  rules/           材料—构件硬规则
  solver/          候选图、最大匹配和排序
  application/     用例、权限、Agent 和工具
  persistence/     SQLAlchemy、Repository 和事务
  api/             FastAPI 路由和身份校验
  mcp_server/      本地只读 MCP Server
  web/             三步网页向导
tests/              自动化测试
migrations/         Alembic 数据库迁移
scripts/            安装、启动和验证脚本
```

技术栈包括 Python、FastAPI、Pydantic、SQLAlchemy、Alembic、PostgreSQL、OpenAI Responses API、MCP Python SDK、Pytest、Docker Compose 和 GitHub Actions。

## 数据与证据边界

公开示例 CSV 来自 TreeTrace_Douglas V1 的几何与节疤字段。它没有提供可以直接证明结构适用性的完整工程评估，因此上传后可能显示“缺少结构评估”。这不是程序错误，也不能通过提示词绕过。

要把一根材料标记为结构可评估，需要同时提供评估方法、评估人员、日期、报告引用，以及强度等级或设计属性引用。详见 [数据与证据](docs/data-and-evidence.md)。

本项目不是结构安全认证软件，也不替代木材分级、工程判断或签章。

## 测试

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,pilot]"
python -m alembic upgrade head
pytest
ruff check .
ruff format --check .
```

也可以运行统一验证脚本：

```powershell
.\scripts\verify-release.ps1
```

GitHub Actions 会在推送和 Pull Request 时运行代码检查、自动化测试和 PostgreSQL 迁移验证。

## 文档

- [使用说明](docs/user-guide.md)
- [系统架构](docs/architecture.md)
- [数据与证据](docs/data-and-evidence.md)
- [产品边界](docs/product-boundaries.md)
- [文件目录](docs/file-index.md)
- [确定性裁决权](docs/adr/0001-deterministic-authority-under-agent-collaboration.md)

## 许可证与数据来源

代码与自有文档使用 MIT License。TreeTrace 派生数据继续遵守原数据集的 CC BY 2.0 条款；来源、转换方法和固定哈希见 [`sample-data/treetrace/README.md`](sample-data/treetrace/README.md)。
