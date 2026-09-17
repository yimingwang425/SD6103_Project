# Resale Flat Prices - Data Cleaning Plan

## 1. Data Overview

- **5 个 CSV 文件**，共 **986,355 行**，时间跨度 1990-01 ~ 2026-09
- 无 null 值（`remaining_lease` 只在 2015+ 的两个文件中存在，其余文件本身没有该列）
- 列分为两种 schema：
  - **1990-1999, 2000-2012, 2012-2014**（10列）：无 `remaining_lease`
  - **2015-2016, 2017-onwards**（11列）：有 `remaining_lease`

## 2. Cleaning Steps

### Step 1: 合并 5 个 CSV

- 用 `pd.concat` 合并所有文件
- 缺少 `remaining_lease` 的文件自动填为 NaN（后续处理）

### Step 2: 去重

- 发现 **1,929 行完全重复**，直接 `drop_duplicates()` 删除

### Step 3: flat_type 统一

| 原始值 | 统一为 |
|--------|--------|
| `MULTI GENERATION` (279行) | `MULTI-GENERATION` |
| `MULTI-GENERATION` (275行) | `MULTI-GENERATION` |

- 即把 `MULTI GENERATION`（无连字符）统一为 `MULTI-GENERATION`

### Step 4: flat_model 大小写统一

原始数据中同一个 model 存在大小写不一致，例如：
- `IMPROVED` / `Improved` → 统一为 **Title Case**（`Improved`）
- `NEW GENERATION` / `New Generation` → `New Generation`
- `MODEL A` / `Model A` → `Model A`
- 等等，共 34 个值 → 统一后 21 个

**规则：** 全部转为 Title Case（`.str.title()`），然后处理特殊情况：
- `Dbss` → `DBSS`（缩写应保持全大写）
- `3Gen` → `3Gen`（已经正确）
- `Type S1`, `Type S2` → 保持不变

### Step 5: remaining_lease 统一格式

三种情况：
1. **NaN**（709,050行）：来自 1990-2014 的文件，没有该列
   - 用 `lease_commence_date` 计算：`remaining_lease_months = (lease_commence_date + 99) * 12 - (month 转换为的年月差)`
   - HDB lease 标准是 99 年
2. **纯数字**（37,153行）：来自 2015-2016 文件，表示剩余年数（整数）
   - 转换为月数：`value * 12`
3. **字符串**（240,152行）：格式如 `"61 years 04 months"`
   - 解析为月数：`years * 12 + months`

**统一输出：`remaining_lease_months`（整数，单位：月）**

### Step 6: month 转为标准日期格式

- 当前格式：`YYYY-MM`（字符串）
- 转为：`datetime` 类型，日设为每月 1 号（`pd.to_datetime(month, format='%Y-%m')`）
- 或保持 `YYYY-MM` 字符串格式（取决于数据库 schema 要求）

### Step 7: storey_range 保持原样

- 虽然存在两套分段标准（`01 TO 03` 三层一段 vs `01 TO 05` 五层一段），但这是原始数据不同时期的记录方式
- **不做合并/转换**，保持原始值，因为转换会丢失精度

### Step 8: 数值列类型检查

| 列 | 类型 | 范围 | 处理 |
|----|------|------|------|
| `floor_area_sqm` | float → int or float | 28 ~ 366.7 | 保留浮点数 |
| `resale_price` | float → int | 5,000 ~ 1,728,000 | 转为整数 |
| `lease_commence_date` | int | 1966 ~ 2023 | 保持整数 |

### Step 9: 字符串列清理

- `town`: 已全大写，**无需处理**
- `block`: 保持原样（有纯数字如 `309`，也有字母如 `1A`）
- `street_name`: 已全大写，**无需处理**

### Step 10: 保留 block + street_name 用于后续 JOIN

- **不生成额外的 key 列**，直接保留 `block` 和 `street_name` 两列
- 后续 Load 阶段通过 `block = blk_no AND street_name = street` JOIN HDB 表获取 `flat_id`
- 这是**多对一**关系：同一栋楼（`block + street_name`）可以有多笔 resale 交易
- HDB 表中 `blk_no + street` 已验证唯一（12,442 行，0 重复），可以安全 JOIN

## 3. Output

- 输出到 `data/processed_data/resale_cleaned.csv`
- 最终列：

| 列名 | 类型 | 说明 |
|------|------|------|
| `month` | str (YYYY-MM) | 交易月份 |
| `town` | str | 城镇 |
| `flat_type` | str | 户型 |
| `block` | str | 楼栋号 |
| `street_name` | str | 街道名 |
| `storey_range` | str | 楼层范围 |
| `floor_area_sqm` | float | 面积 |
| `flat_model` | str | 户型模型 |
| `lease_commence_date` | int | 租约起始年 |
| `remaining_lease_months` | int | 剩余租约（月） |
| `resale_price` | int | 转售价格 |

## 4. Not in Scope (后续阶段处理)

- `flat_id` 外键：等 HDB 表清洗完成后，通过 `block + street_name` JOIN 获取
- 数据库导入（Load 阶段）
