# 数据与证据

## 为什么会显示“缺少结构评估”

材料尺寸足够，并不代表它已经被证明适合结构用途。系统把“没有证据”和“证据证明不合格”分开处理：

- `eligible`：有可追溯评估，允许进入已验证候选图；
- `restricted`：只允许特定用途，当前需要额外许可记录；
- `ineligible`：明确不适合结构用途；
- `not_evaluable`：没有足够记录进行判断。

TreeTrace 示例 CSV 提供几何尺寸和节疤字段，没有提供完整的工程评估。因此它可以用于展示导入、几何匹配和表面偏好，但默认不能形成结构安全结论。

## 结构评估字段

当 `structural_use_status` 为 `eligible` 或 `restricted` 时，以下字段必须同时存在：

| 字段 | 含义 | 示例 |
|---|---|---|
| `assessment_method` | 评估方法 | `engineering_assessment` |
| `assessor_id` | 人员或机构编号 | `ENGINEER-001` |
| `assessment_date` | 评估日期 | `2026-10-14` |
| `assessment_evidence_ref` | 报告或记录引用 | `REPORT-001` |
| `strength_grade` | 强度等级 | `C24` |
| `design_property_ref` | 设计属性引用 | 与强度等级至少提供一项 |

这些字段之间有一致性校验。只填 `eligible` 而不提供评估来源，导入会被拒绝。

## 缺陷字段

主要字段包括：

- `defect_data_status`：`complete`、`partial` 或 `unknown`；
- `defect_evidence_ref`：缺陷记录引用；
- `defects`：缺陷类型、严重程度、相对位置和来源；
- `surface_knots`：表面节疤的面、范围、类型代码、尺寸和来源。

梁构件会检查中段区域是否存在严重缺陷。如果记录不完整、严重程度未知或位置未知，规则返回 `not_evaluable`。

表面节疤用于外观软排序，不会自动转换成结构缺陷等级。

## CSV 示例

```csv
material_id,length_mm,section_width_mm,section_height_mm,species,structural_use_status,strength_grade,assessment_method,assessor_id,assessment_date,assessment_evidence_ref,defect_data_status,defect_evidence_ref,availability_status
TT-B001,4179,45.8,144.2,Douglas fir,eligible,C24,engineering_assessment,ENGINEER-001,2026-10-14,REPORT-001,complete,DEFECT-REPORT-001,available
```

上面的编号只是字段格式示例，不是对示例木料的真实评估。真实数据必须引用实际存在的记录。

## 复核流程

```text
现场检查或检测
      ↓
形成可追溯报告
      ↓
补充材料字段
      ↓
创建新的库存版本
      ↓
重新运行规则与匹配
```

当前三步网页负责读取和展示结果，不提供在线工程签章。专业责任仍在系统外完成，系统只消费已经形成的结构化记录。
