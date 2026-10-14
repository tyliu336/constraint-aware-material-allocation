# 使用说明

## 1. 准备环境

系统要求：

- Windows PowerShell；
- Python 3.12 或更高版本；
- 一个可调用 OpenAI API 的 key；
- 一个当前账号可以使用的模型 ID。

在项目根目录执行：

```powershell
.\scripts\bootstrap-dev.ps1 -SkipTreeTrace
```

脚本会创建 `.venv`、安装项目依赖并准备本地数据库。API key 不要写入源码、CSV、`.env` 或命令历史。可以在当前 PowerShell 会话中安全输入：

```powershell
$secureKey = Read-Host 'OpenAI API key' -AsSecureString
$keyPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
$env:MAC_OPENAI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPtr)
$env:MAC_REQUIREMENT_MODEL = '<你的账号可用的模型 ID>'
```

## 2. 启动服务

```powershell
.\scripts\start-demo.ps1
```

终端会打印：

- 本地项目 ID；
- 本地用户 ID；
- 一次性 Bearer token；
- 页面地址 `http://127.0.0.1:8000/`。

保持这个终端运行。打开页面后，在“本地连接”中粘贴 token 并确认。

## 3. 木料录入

有两种方式：

### 使用内置演示材料

点击“载入演示材料”。这些记录会带有合成的评估字段，只用于检查完整页面流程，不代表真实工程证据。

### 上传 CSV

推荐先使用：

```text
sample-data/treetrace/treetrace-boards-demo.csv
```

最少需要以下字段：

```text
material_id,length_mm,section_width_mm,section_height_mm
```

可以增加树种和表面节疤字段：

```text
species,knotTypeWideFaceWhole,knotSizeWideFaceWhole_mm,knotTypeNarrowFaceWhole,knotSizeNarrowFaceWhole_mm
```

TreeTrace 示例数据没有完整的结构评估。上传后出现“缺少结构评估”是预期行为，表示尺寸上可以形成候选，但不能声称已经验证适合承重。

## 4. 描述结构

1. 设置 X、Y、Z 方向的点数和点间距；
2. 在点阵中依次点击两个节点，建立一根构件；
3. 为构件选择角色，如梁、柱或支撑；
4. 检查构件清单中的长度和截面要求；
5. 如有误，可以撤销最后一根或清空后重画。

点阵用于描述分配需求，不进行结构力学计算。构件长度来自两个节点之间的三维距离。

## 5. 输入偏好

可以输入一句普通中文，例如：

```text
优先减少切割余料，同时尽量少用表面节疤较大的木料。
```

当前支持的含义只有：

- 减少切割余料；
- 保留稀缺长料；
- 少用记录不完整的材料；
- 减少已记录的表面节疤。

服务端把这句话发送给配置的 OpenAI 模型，要求模型只返回受支持的结构化偏好。模型不能更改尺寸、等级、可用状态和缺陷规则。

点击“生成分配方案”后，页面显示模型识别出的权重以及最多三个候选方案。

## 6. 阅读结果

结果状态分为：

| 页面状态 | 含义 |
|---|---|
| 已找到可验证完整匹配 | 所有被采用的材料—构件组合均通过现有硬规则 |
| 已有候选，需人工复核证据 | 只有把未知记录暂时视为可能合格时，才能覆盖全部构件 |
| 当前材料无法覆盖全部构件 | 即使考虑待复核材料，也找不到完整匹配 |

方案表中的“结构评估”只说明材料是否有可追溯的结构适用记录。它不是 LLM 置信度，也不等于节疤数量。

## 7. 完成人工复核

当前网页不提供工程人员在线签章。实际操作是：

1. 由有能力的人员检查木料；
2. 形成外部检测或评估记录；
3. 在材料数据中填写评估字段；
4. 作为新的库存版本重新导入；
5. 让系统重新执行规则和匹配。

具体字段见 [数据与证据](data-and-evidence.md)。不要为了让页面变绿而编造评估人员、报告编号或缺陷结论。

## 8. 停止与清理

在运行服务的终端按 `Ctrl+C`，然后执行：

```powershell
Remove-Item Env:MAC_OPENAI_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:MAC_REQUIREMENT_MODEL -ErrorAction SilentlyContinue
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPtr)
```

本地 SQLite 数据库属于运行数据，不应提交到 Git。
