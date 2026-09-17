# Resale Transaction 当前结果说明

本 README 对应 `resale_result_years` 中已生成的年单位结果，记录实际处理数量、排除明细、字段变更及核验结果。

- 结果目录：`C:\myproject\NTU\resale_result_years`
- 原始目录：`C:\Users\yinmo\Desktop\ResaleFlatPrices`
- 脚本：`C:\myproject\NTU\NTU HDB.py`
- 结果文件最后修改时间：2026-09-17 16:05:30（北京时间）。
- 本 README 核验日期：2026-09-17。
- 当前 `RemainingLease` 单位：**完整年，整数，不乘 12**。
- 本次编写 README 只读取现有原始文件和结果，没有重新执行清洗，也没有修改数据文件。

## 1. 处理前后行数

所有数字均为 **CSV 数据记录数，不包含表头**。核验使用 CSV 解析器计数，并逐条关联原始记录、主结果及审计文件，不以文件的物理文本行数代替记录数。

| 项目 | 实际数量 |
| --- | ---: |
| 原始 CSV 文件 | 5 个 |
| 原始记录合计 | **986,771 行** |
| 最终主结果 `resale_transactions.csv` | **986,720 行 × 13 列** |
| 未进入主结果、转存异常文件 | **51 行** |
| 主结果保留比例 | 99.9948% |
| 异常隔离比例 | 0.0052% |
| `audit.csv` 审计记录 | 986,720 行 |
| 疑似重复的后续记录，全部保留 | 1,929 行 |
| 因疑似重复而删除的记录 | **0 行** |
| 主结果交易年月范围 | 1990-01 至 2026-09 |
| 主结果剩余租期范围 | 39—99 年 |

行数核对：**986,771 = 986,720 + 51**。

### 分文件行数

| 来源 | 处理前 | 进入主结果 | 异常隔离 |
| --- | ---: | ---: | ---: |
| F1：1990—1999（批准日期） | 287,196 | 287,160 | 36 |
| F2：2000—2012-02（批准日期） | 369,651 | 369,636 | 15 |
| F3：2015—2016（登记日期） | 37,153 | 37,153 | 0 |
| F4：2012-03—2014（登记日期） | 52,203 | 52,203 | 0 |
| F5：2017 起（登记日期） | 240,568 | 240,568 | 0 |
| **合计** | **986,771** | **986,720** | **51** |

文件编号与实际文件名对应如下；附录的排除明细使用同一编号。

- **F1**：`Resale Flat Prices (Based on Approval Date), 1990 - 1999.csv`
- **F2**：`Resale Flat Prices (Based on Approval Date), 2000 - Feb 2012.csv`
- **F3**：`Resale Flat Prices (Based on Registration Date), From Jan 2015 to Dec 2016.csv`
- **F4**：`Resale Flat Prices (Based on Registration Date), From Mar 2012 to Dec 2014.csv`
- **F5**：`Resale flat prices based on registration date from Jan-2017 onwards.csv`

## 2. 怎么处理的

1. 读取上述五个 CSV，校验必需列。按相对文件名排序，再按各文件原始记录顺序处理，不按成交年月重新排序。
2. 从 `month` 提取 `TransactionYear` 和 `TransactionMonth`。批准日期与登记日期各自沿用来源口径，审计中分别标记为 `approval`、`registration`。
3. 规范关联属性：去除文本首尾空白、合并连续空白、统一大写；保留楼栋编号的字母和前导零；将楼层区间拆成最小值、最大值；规范面积表示。
4. 将已有租期转换为完整年；原始缺失租期用 `99 - (交易年份 - 起租年份)` 补算。
5. 使用 Decimal 检查价格并输出两位小数。检查数值范围、年月、楼层及估算前提。
6. 输出通过规则的记录及其预先分配的 `TransactionID`，生成 13 列主结果。疑似重复只标记，不删除。
7. 不满足规则的记录写入 `rejected_rows.csv`，保留原因和原始内容；输出逐条审计及汇总报告。

`TransactionID` 实际按每一条输入记录分配序号，从 1 开始，包括被隔离的记录。因此主结果编号范围为 1—986,771，其中有 51 个序号间隔。它是本次输入顺序对应的记录编号，不是官方交易 ID；输入文件名、顺序或记录集合改变后，重新运行的编号可能变化。

## 3. 哪些记录没有进入主结果

本版没有修改或删除原始 CSV 中的记录。这里的“排除”是指记录未进入主结果，同时完整保存在 [rejected_rows.csv](rejected_rows.csv)。

排除的 **51 条**全部只有同一种原因：

```text
ErrorCode: invalid_lease_estimate
Reason: 租约起始年晚于交易年，无法按年估算
```

这些记录原本没有剩余租期，且 `lease_commence_date > TransactionYear`。直接套用公式会得到超过 99 年的结果，因此当前脚本将其隔离，没有填成 99、0 或其他值。

- F1：36 条。
- F2：15 条。
- 起租年份与交易年份差异：晚 1 年：47 条；晚 2 年：4 条。
- 本次没有因为价格、面积、楼层、文本属性或其他原因额外排除记录。
- 全部 51 条的位置见第 8 节；异常文件包含对应的完整原始记录 JSON。

这表示它们**不满足本版估算规则**，不能仅据此认定原始交易无效。尤其这些记录来自按批准日期统计的文件，日期口径与起租年份的关系需要另行核实。本版没有擅自修正这些年份。

## 4. 哪些字段被修改

以下计数仅针对最终进入主结果的 **986,720 条**记录。不同字段的修改可能发生在同一行，计数不能相加当作修改的记录总数。

| 字段或操作 | 处理方式 | 实际影响 |
| --- | --- | ---: |
| TransactionID | 新增记录编号 | 986,720 个输出编号 |
| month | 拆为整数 TransactionYear、TransactionMonth；年月含义不变 | 986,720 行 |
| RemainingLease：缺失值 | 按 99 年公式计算，单位为年 | **708,999 行补齐** |
| RemainingLease：已有值 | 读取原值，规范为完整年 | 277,721 行 |
| 已有租期中的非零月份 | 主结果舍去不足一年的部分，原文保留在 audit.csv | **220,828 行** |
| storey_range | 拆为 storey_range_min、storey_range_max，例如 01 TO 03 → 1、3 | 986,720 行 |
| flat_type | 标准化文本，实际将 MULTI GENERATION 改为 MULTI-GENERATION | 279 行文本变化 |
| flat_model | 标准化文本，主要统一大小写，例如 Improved → IMPROVED | 695,473 行文本变化 |
| town | 执行文本规范化，本次输出与原值相同 | 0 行文本变化 |
| block | 按文本处理，本次输出与原值相同 | 0 行文本变化 |
| street_name | 执行文本规范化，本次输出与原值相同 | 0 行文本变化 |
| floor_area_sqm | 去除无意义的小数尾零，例如 68.00 → 68 | 123,892 行表示变化；数值变化 0 行 |
| resale_price → ResalePrice | 更名，统一为两位小数，例如 9000 → 9000.00 | 986,572 行表示变化；金额变化 0 行 |

价格和面积已经对全部保留记录用 Decimal 比较过，**没有改变数值**。价格格式本来就是两位小数的 148 行，其文字表示也未变化。

### RemainingLease 的具体口径

```python
# 只用于原始 remaining_lease 缺失的记录
RemainingLease = 99 - (TransactionYear - lease_commence_date)
```

不乘 12。例如交易年月为 1990-12、起租年份为 1977，结果为 **86 年**。由于原始起租信息只有年份，补算采用交易年份，不扣除交易当年的月份，不声称具有月级精度。

原始数据中共有 **709,050 条缺失租期**：其中 708,999 条成功补算，51 条因起租年晚于交易年而隔离。原本已有租期的 277,721 条优先使用原值，不用估算公式覆盖。

已有租期转换示例：

- `70` → `70` 年。
- `61 years 04 months` → `61` 年。
- `61 years 08 months` → `61` 年。

现有租期中，220,828 条包含非零月份，这部分月份在整数年主结果中被舍去，不四舍五入。原始字符串仍可通过 `audit.csv` 的 `OriginalRemainingLease` 追溯。

## 5. 最终字段和没有放入主表的字段

最终主结果列顺序固定为：

```text
TransactionID,TransactionYear,TransactionMonth,RemainingLease,ResalePrice,town,flat_type,block,street_name,storey_range_min,storey_range_max,floor_area_sqm,flat_model
```

| 字段 | 目标类型 / 单位 |
| --- | --- |
| TransactionID | INT，记录级主键 |
| TransactionYear | INT，年 |
| TransactionMonth | INT，月，1—12 |
| RemainingLease | INT，完整年 |
| ResalePrice | NUMERIC(12,2) |
| town、flat_type、block、street_name、flat_model | 文本 |
| storey_range_min、storey_range_max | INT，楼层 |
| floor_area_sqm | NUMERIC，平方米 |

CSV 本身不存储数据库类型，导入时需要自行声明相应类型，尤其 block 应按文本导入。

原始字段的去向：

- `month`：由 TransactionYear、TransactionMonth 替代。
- `storey_range`：由 storey_range_min、storey_range_max 替代，原始文本另存审计文件。
- `remaining_lease`：更名并转换为 RemainingLease，原始文本另存审计文件。
- `resale_price`：更名为 ResalePrice。
- `lease_commence_date`：用于缺失租期计算，未放入 13 列主表；保存在审计文件的 LeaseCommenceYear。
- `FlatID`：本版没有生成，也没有执行外键匹配。关联属性已保留，供后续处理。

这些是字段拆分、替换或迁移，不是额外删除数据行。

## 6. 疑似重复记录如何处理

本次共标记 **1,929 条后续疑似重复记录**。这个数字不是重复组数，也不包含各组首次出现的记录。所有这些记录都保留在主结果中。

检测比较标准化后的房屋属性、租约起始年、交易年月、租期原始年数及余月、成交价。即使 61 年 4 月与 61 年 8 月都输出为 61 年，重复检测仍区分原始余月。原始数据没有唯一的真实交易标识，因此本版不直接根据公开属性相同而删行。

可在 `audit.csv` 中筛选 `PotentialDuplicate = 1` 查看这 1,929 条记录，`FirstMatchingTransactionID` 指向最先出现的相同组合。

## 7. 文件用途和核验范围

| 文件 | 数据记录数 | 用途 |
| --- | ---: | --- |
| [resale_transactions.csv](resale_transactions.csv) | 986,720 | 13 列交易主结果 |
| [audit.csv](audit.csv) | 986,720 | 每条主结果的来源、记录序号、日期口径、租期来源和原文、重复标记 |
| [rejected_rows.csv](rejected_rows.csv) | 51 | 被隔离记录的原因及完整原始内容 |
| [quality_report.json](quality_report.json) | 不适用 | 运行配置、逐文件数量及质量统计 |

本 README 的数字取自本次实际文件，并经过全量只读核对：

- 逐文件重新计数原始记录，逐条核对主结果、audit.csv 和原始来源位置。
- 核对所有输出编号、交易年月、文本转换、楼层 min/max、剩余租期公式、价格和面积。
- 核对所有异常记录的原始 JSON、具体原因及原始记录序号。
- 核对所有主结果与审计行数，确认疑似重复标记的记录仍在主结果内。
- 所有行数与 quality_report.json 一致；没有发现未计入主结果或异常文件的遗漏记录。

当前脚本 SHA-256：

```text
8d57182519bcd42bec544be790d1152c5c5f526ea994f2a4888bf0c48d110f00
```

上述规则与数量仅描述这份现有结果。后续修改脚本或原始数据后重新生成结果时，应重新核对并更新本 README。

## 8. 全部 51 条异常隔离明细

下表中的记录序号是该源 CSV **表头之后从 1 开始的逻辑记录序号**，不是 TransactionID，也不是包含表头的物理文本行号。所有行的原因均为“租约起始年晚于交易年”。F1、F2 的完整文件名见第 1 节。

| 来源 | 原始记录序号 | 交易年月 | 起租年份 | town | block | street_name |
| --- | ---: | --- | ---: | --- | --- | --- |
| F1 | 23272 | 1991-11 | 1992 | BISHAN | 227 | BISHAN ST 23 |
| F1 | 34247 | 1992-08 | 1993 | TAMPINES | 476 | TAMPINES ST 44 |
| F1 | 46536 | 1993-06 | 1994 | WOODLANDS | 321 | WOODLANDS ST 32 |
| F1 | 69034 | 1994-05 | 1995 | CHOA CHU KANG | 528 | CHOA CHU KANG ST 51 |
| F1 | 69610 | 1994-05 | 1995 | PASIR RIS | 631 | PASIR RIS DR 3 |
| F1 | 69978 | 1994-05 | 1996 | TAMPINES | 332 | TAMPINES ST 32 |
| F1 | 71710 | 1994-06 | 1995 | PASIR RIS | 143 | PASIR RIS ST 11 |
| F1 | 76425 | 1994-08 | 1995 | WOODLANDS | 136 | MARSILING RD |
| F1 | 77302 | 1994-09 | 1995 | CHOA CHU KANG | 503 | CHOA CHU KANG ST 51 |
| F1 | 77903 | 1994-09 | 1995 | QUEENSTOWN | 150 | MEI LING ST |
| F1 | 78075 | 1994-09 | 1995 | TAMPINES | 322 | TAMPINES ST 33 |
| F1 | 78153 | 1994-09 | 1995 | TAMPINES | 842D | TAMPINES ST 82 |
| F1 | 78276 | 1994-09 | 1995 | WOODLANDS | 847 | WOODLANDS ST 82 |
| F1 | 79665 | 1994-10 | 1995 | PASIR RIS | 158 | PASIR RIS ST 13 |
| F1 | 80080 | 1994-10 | 1995 | WOODLANDS | 326 | WOODLANDS ST 32 |
| F1 | 81481 | 1994-11 | 1995 | PASIR RIS | 601 | ELIAS RD |
| F1 | 83394 | 1994-12 | 1995 | JURONG WEST | 732 | JURONG WEST ST 73 |
| F1 | 83395 | 1994-12 | 1995 | JURONG WEST | 261 | BOON LAY DR |
| F1 | 83495 | 1994-12 | 1995 | KALLANG/WHAMPOA | 114 | WHAMPOA RD |
| F1 | 83567 | 1994-12 | 1995 | PASIR RIS | 611 | ELIAS RD |
| F1 | 83970 | 1994-12 | 1996 | TAMPINES | 335 | TAMPINES ST 32 |
| F1 | 87451 | 1995-02 | 1996 | PASIR RIS | 759 | PASIR RIS ST 71 |
| F1 | 90799 | 1995-04 | 1996 | PASIR RIS | 715 | PASIR RIS ST 72 |
| F1 | 91218 | 1995-04 | 1996 | WOODLANDS | 822 | WOODLANDS ST 82 |
| F1 | 94415 | 1995-06 | 1996 | QUEENSTOWN | 132 | CLARENCE LANE |
| F1 | 100728 | 1995-09 | 1996 | CHOA CHU KANG | 556 | CHOA CHU KANG NTH 6 |
| F1 | 101457 | 1995-09 | 1996 | PASIR RIS | 775 | PASIR RIS ST 71 |
| F1 | 104252 | 1995-10 | 1996 | PASIR RIS | 753 | PASIR RIS ST 71 |
| F1 | 108149 | 1995-11 | 1996 | WOODLANDS | 874 | WOODLANDS ST 82 |
| F1 | 108753 | 1995-12 | 1996 | ANG MO KIO | 253 | ANG MO KIO ST 21 |
| F1 | 110540 | 1995-12 | 1996 | PASIR RIS | 738 | PASIR RIS DR 10 |
| F1 | 110560 | 1995-12 | 1996 | PASIR RIS | 724 | PASIR RIS ST 72 |
| F1 | 111073 | 1995-12 | 1996 | TAMPINES | 357 | TAMPINES ST 33 |
| F1 | 121059 | 1996-04 | 1997 | BUKIT PANJANG | 453 | FAJAR RD |
| F1 | 126052 | 1996-06 | 1997 | BEDOK | 660 | JLN TENAGA |
| F1 | 127932 | 1996-06 | 1997 | TAMPINES | 737 | TAMPINES ST 72 |
| F2 | 157847 | 2004-09 | 2006 | BUKIT MERAH | 112A | DEPOT RD |
| F2 | 162627 | 2004-11 | 2006 | BUKIT MERAH | 109B | DEPOT RD |
| F2 | 167747 | 2005-01 | 2006 | BUKIT MERAH | 111A | DEPOT RD |
| F2 | 167748 | 2005-01 | 2006 | BUKIT MERAH | 112B | DEPOT RD |
| F2 | 172853 | 2005-03 | 2006 | BUKIT MERAH | 109A | DEPOT RD |
| F2 | 175739 | 2005-04 | 2006 | BUKIT MERAH | 111B | DEPOT RD |
| F2 | 178192 | 2005-05 | 2006 | BUKIT MERAH | 111B | DEPOT RD |
| F2 | 180142 | 2005-06 | 2006 | ANG MO KIO | 316A | ANG MO KIO ST 31 |
| F2 | 182636 | 2005-07 | 2006 | ANG MO KIO | 315A | ANG MO KIO ST 31 |
| F2 | 182637 | 2005-07 | 2006 | ANG MO KIO | 316A | ANG MO KIO ST 31 |
| F2 | 185030 | 2005-08 | 2006 | ANG MO KIO | 315A | ANG MO KIO ST 31 |
| F2 | 185501 | 2005-08 | 2006 | BUKIT MERAH | 111A | DEPOT RD |
| F2 | 187562 | 2005-09 | 2006 | ANG MO KIO | 309A | ANG MO KIO ST 31 |
| F2 | 187996 | 2005-09 | 2006 | BUKIT MERAH | 111B | DEPOT RD |
| F2 | 347382 | 2011-02 | 2012 | QUEENSTOWN | 28B | DOVER CRES |
