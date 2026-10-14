# TreeTrace 简化测试数据

本目录提供两个从 TreeTrace_Douglas V1 `boards.txt` 生成的产品兼容派生文件。它们保留 Douglas fir 板材的标识、长度、截面尺寸，以及整板宽面/窄面的节疤类型和尺寸，可直接上传到本项目根路径的三步向导。

| 文件 | 记录数 | 用途 |
|---|---:|---|
| `treetrace-boards-demo.csv` | 80 | 推荐给 GitHub 访问者；可以在最多 30 个构件时保持候选组合不超过 2,500 条试点排序上限 |
| `treetrace-boards-simple.csv` | 346 | 完整板材几何派生集；用于全量导入、字段和来源验证，较多构件同时排序时可能触发有界计算保护 |

## 直接使用

1. 启动项目并打开 `http://127.0.0.1:8000/`。
2. 在“上传材料”区域选择 `treetrace-boards-demo.csv`。
3. 页面应显示 80 根材料，不再出现 `length_mm 必须是正数`，并可继续生成常规演示结构的方案。

如果要确认全部数据均可导入，再单独上传 `treetrace-boards-simple.csv`。Top-K 排序受 30 个构件和 2,500 条候选边的试点预算保护；例如 346 根材料与 20 个构件会形成 6,760 条可排序候选边，因此后端会明确拒绝无界扩张，而不是静默截断材料。

CSV 字段如下，所有尺寸单位均为毫米：

```text
material_id,length_mm,section_width_mm,section_height_mm,species,knotTypeWideFaceWhole,knotSizeWideFaceWhole_mm,knotTypeNarrowFaceWhole,knotSizeNarrowFaceWhole_mm
```

## 转换规则

| 输出字段 | TreeTrace 来源 | 转换 |
|---|---|---|
| `material_id` | `boardID` | `TT-B{boardID:03d}` |
| `length_mm` | `length_m` | 乘以 1000 |
| `section_width_mm` | `thickness_m` | 乘以 1000 |
| `section_height_mm` | `width_m` | 乘以 1000 |
| `species` | 数据集身份 | 固定为 `Douglas fir` |
| `knotTypeWideFaceWhole` | 同名字段 | 整板宽面节疤代码 |
| `knotSizeWideFaceWhole_mm` | 同名字段 | 整板宽面节疤尺寸 |
| `knotTypeNarrowFaceWhole` | 同名字段 | 整板窄面节疤代码 |
| `knotSizeNarrowFaceWhole_mm` | 同名字段 | 整板窄面节疤尺寸 |

页面把四个节疤字段转换成结构化表面证据，用于 `minimize_surface_defects` 软排序。评分只表示现有记录中的相对节疤情况，不等于完整表面检测或“无瑕疵”证明。

转换没有加入强度等级、结构合格状态、实时可用性、回收历史或污染信息。普通 CSV 进入简单页面后，结构状态保持 `not_evaluable`；页面为了完成演示流程，会把普通上传记录的会话内可用状态初始化为 `available`，这不是 TreeTrace 提供的实时库存事实。这份数据适合演示几何、表面节疤偏好和约束流程，不是结构安全认证或真实库存证明。

## 来源、署名与许可

数据集引用：Longuetaud, Fleur; Pot, Guillaume; Mothe, Frédéric; Barthelemy, Alexis; Decelle, Rémi; Delconte, Florian; Ge, Xihe; Guillaume, Grégoire; Mancini, Théo; Ravoajanahary, Tojo; Butaud, Jean-Claude; Collet, Robert; Debled-Rennesson, Isabelle; Marcon, Bertrand; Ngo, Phuc; Roux Benjamin; Viguier, Joffrey, 2022, “TreeTrace_Douglas”, <https://doi.org/10.15454/YUNEGL>, Recherche Data Gouv, V1.

官方数据集页面要求按上述数据集引用署名，并将数据条款列为 [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/fr/legalcode)。本目录中的 CSV 是该数据集的派生内容，继续受数据集条款约束。项目根目录的 MIT License 只覆盖本项目代码与自有文档，不重新授权该数据。

## 可重复生成与校验

先恢复经过哈希校验的官方表格，再生成派生 CSV：

```powershell
.\scripts\fetch-treetrace-tables.ps1
.\.venv\Scripts\python.exe .\scripts\build-treetrace-sample.py
.\.venv\Scripts\python.exe .\scripts\build-treetrace-sample.py `
  --limit 80 `
  --output .\sample-data\treetrace\treetrace-boards-demo.csv
```

生成脚本只接受已审计的 TreeTrace V1 `boards.txt` SHA-256，并检查 346 条记录、唯一 ID 和全部正尺寸。固定哈希见 `source-hashes.txt`。
