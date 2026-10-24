# Hawker-Geo 工作流交接说明

Data Systems Project · AY26S1 · 负责范围：**hawker centre 地理数据 + HDB↔Hawker 步行距离**

本目录包含三件事：① 作业要求在我们这一块到底要交付什么；② hawker 数据的清洗结果与决策记录；③ 用免费 OSM 步行路网接口算真实步行距离的完整方案与实测数据。

---

## 1. 作业要求拆解：我们这块要交什么

先把 PDF 里跟我们相关的硬要求抽出来，避免做到一半才发现漏了东西。

| 出处 | 原文要求 | 我们对应的产出 |
|---|---|---|
| Task 1.1 | "To link HDB flats with nearby hawker centres, you should define **three distinct levels of proximity**" | 三级 proximity 定义（见 §4） |
| Task 1.1 | "You will need to **model the chosen proximity in your ER model** as well" | HDB Flat ↔ Hawker Centre 的 M:N 关系 + 关系上的属性（距离、等级） |
| Task 1.1 | "other options **may require additional data or API calls**" | 这句话是教授在明示：想拿高分就得调外部接口。我们正好走这条路 |
| Task 1.2 | "Map the ER diagram to a relational schema in 3NF/BCNF"、"defining constraints as precisely as possible" | `hawker_centre`、`hawker_proximity` 两张表的 DDL（见 `04_schema_hawker.sql`） |
| Task 2.1 | "write code for automated and reproducible parsing, extraction, and transformation" | `01_clean_hawker_geo.py` |
| Task 2.1 | "you may need to perform additional computations, use **external services**, or supplementary datasets to acquire data for the proximity levels" | `02_compute_walk_distance.py`（OSRM 步行路网） |
| Task 2.2 | "produce a **.csv file for each table**" | `output/` 下三份 CSV，可直接 `\copy` |
| Q4 | 最近 proximity class 与最远 class 的价格差 | 需要「每套房自己的 class」= 最近 hawker 的 class |
| Q5 | "flats that are in proximity to a hawker **with fresh market stalls**" | 需要 `no_of_mkt_produce_stalls > 0`，这个字段**只在官方数据集里有**，geo 文件没有 |
| Task 3/4 | 查询性能随数据量变化、加索引 | `hawker_proximity` 的行数直接决定 Q4/Q5 的 runtime 和索引设计 |

**三个容易踩的坑：**

1. **Q5 的字段不在 geo 文件里。** `hawker_geo.csv` 只有 `longitude, latitude, NAME, ADDRESS_MYENV` 四列。要判断「有 fresh market stalls」必须去 data.gov.sg 的 Hawker Centres 数据集拿 `no_of_mkt_produce_stalls`。这一步 join 是必须做的，不是可选项。
2. **Q4 的「class」是套房的属性，不是 pair 的属性。** 每套房会同时靠近好几个 hawker，得先定义「这套房的 class = 它最近那个 hawker 的 class」，否则每套房按定义都属于 class 1，Q4 就没得比了。
3. **proximity 是 M:N 关系，必须单独建表。** 不能把 class 塞进 HDB Flat 表里——那样违反了 3NF（一套房有多个 hawker，会重复）。

---

## 2. 数据清洗：发现了什么、怎么处理

### 2.1 原始数据体检

`hawker_geo (1).csv`：129 行 × 4 列。**结构性问题是干净的**——没有重复行、没有非法坐标、没有空名字、没有超出新加坡经纬度范围的点。真正的麻烦在「和官方数据集对不上」。

### 2.2 十个具体问题

| # | 问题 | 规模 | 处理方式 |
|---|---|---|---|
| 1 | geo 文件 129 行，官方数据集只有 **107** 行 | 差 22 | 以 geo 为准（它才有坐标），官方数据 LEFT JOIN 补属性 |
| 2 | 多出来的行全是官方快照之后新开的 hawker centre（Punggol Coast、Bukit Canberra、Senja、Buangkok、One Punggol…） | 23 行匹配不上 | 保留，属性置 NULL，**不当作 0**（根因见 §5.6） |
| 3 | `ADDRESS_MYENV` 为空 | 17 行 | 保留 NULL，**不编造地址**；这 17 行也全部没有 postal code |
| 4 | 官方有、geo 没有的中心 | 1 个：Blk 4A Woodlands Centre Road | 记录为已知缺口，无法算距离（没有坐标） |
| 5 | 两边**命名习惯完全不同** | 几乎全部 | 例：官方 `Blk 79 Redhill Lane` ↔ geo `Redhill Lane Blk 79 (Redhill Market)`；官方 `Tekka Market` ↔ geo `Buffalo Road Blk 665 (Tekka Centre/Zhu Jiao Market)` |
| 6 | 名字里带括号别名 | 约 70 行 | 拆成 `name`（Block+街道）+ `alias`（俗称），两个字段都有用 |
| 7 | 不可见字符：NBSP（U+00A0） | 1 行 | `"Singapore\xa0738623"` → 替换为普通空格 |
| 8 | 逗号后缺空格 | 若干 | `"Blk 4,Woodlands St 12,Singapore 738623"` → 统一成 `", "` |
| 9 | 坐标小数位不一致 | 7 / 13 / 14 位混用 | 统一 `ROUND(..., 6)`（约 0.11 m，远超步行距离需要的精度） |
| 10 | Redhill Lane Blk 79 与 Blk 85 相距 **59.7 m** | 1 对 | **不合并**：官方数据里是两个独立中心（Blk 79 是纯菜市场 MK，122 个菜摊；Blk 85 是熟食中心 HC，96 个熟食摊） |

### 2.3 Join 策略：为什么用 postal code 而不是名字

名字匹配在这个数据集上不可靠——`STOPWORDS` 一删（market / food / centre / hawker 都是通用词），token 集合就退化成 `{bukit, timah}` 这种极短集合，Jaccard 直接飙到 1.0。

实测踩过的**两个假匹配**（已经修掉，代码里保留了注释）：

- `Bukit Timah Interim Hawker Centre and Market` ✗→ `Bukit Timah Market`
  两者相距约 200 m，是新旧两个设施，不是同一个。
- `Chong Pang Hawker Centre & Market` ✗→ `Chong Pang Market & Food Centre`
  官方这条已经被 geo 里的 `Yishun Ring Road Blk 104/105` 通过 postal 认领了，再匹配就是多对一。

**最终三级匹配：**

```
Pass 1  postal code 精确匹配    → 104 行   （已验证 1:1，无多对一）
Pass 2  人工 alias 表          →   2 行   （改名 / 搬迁的两个）
Pass 3  Jaccard ≥ 0.80 + 一对一并发 → 0 行  （作为未来数据更新的安全网）
────────────────────────────────────────
合计匹配 106 / 107 个官方中心
```

两个必须手工处理的是：

| geo 名字 | 官方名字 | 原因 |
|---|---|---|
| Market Street Hawker Centre | Market Street Food Centre | 改名（Golden Shoe 停车场楼上） |
| Tanglin Halt Hawker Centre & Market | Blks 1A/ 2A/ 3A Commonwealth Drive | 新中心，官方用 Block 号命名 |

### 2.4 结果校验

- 官方有 **81** 个中心 `no_of_mkt_produce_stalls > 0`，我们的表里 `has_fresh_market = true` 也正好 **81** 个 ✅
- 106 行里 **全部** 满足 `熟食摊 + 菜摊 = 总摊位`，可以直接写成 CHECK 约束 ✅

---

## 3. 步行距离：免费接口调研与实测

### 3.1 为什么必须做

作业原文允许「distance-based definition」，但同时也写了 "other options may require additional data or API calls"。用直线距离（haversine）的问题是：它穿过组屋、水渠、快速路和地铁轨道。500 m / 1000 m 这种阈值上，误差会直接改变分类结果——**而 Q4、Q5 的结论完全建立在分类上**。

### 3.2 免费接口横评（全部实测过，2026-10）

| 服务 | 步行 profile | 单次上限 | 需要 key | 实测延迟 | 结论 |
|---|---|---|---|---|---|
| **OSRM demo（FOSSGIS）**<br>`routing.openstreetmap.de/routed-foot` | ✅ `foot` | 100 个坐标 | ❌ 不需要 | 0.8–10 s | **采用**。唯一能一次塞进 100 个点还能算步行矩阵的免费服务 |
| Valhalla demo<br>`valhalla1.openstreetmap.de` | ✅ `pedestrian` | **矩阵 ≤ 100 格**（10×10 OK，10×11 报 400） | ❌ | 0.6–0.8 s | 快但上限太小，10 万对要发 1000 次请求，不现实 |
| OpenRouteService | ✅ `foot-walking` | 3500 个点 | ✅ 需注册 | — | 能力最强，但要申请 key；2000 req/day 够用。**备选方案** |
| GraphHopper | ✅ `foot` | 免费层单次路由仅 5 个点 | ✅ 需注册（免费层 500 credits/天，**限非商业**） | — | 见下方额度测算：跑完全量要 165 天，**只适合抽检** |
| OneMap（新加坡政府，SLA）<br>`/api/public/routingsvc/route` | ✅ `routeType=walk` | **一次只能算 1 对**，无矩阵接口 | ✅ 需 token（免费注册，选 Education + Research/Student Projects） | — | **新加坡官方路网**，OSM 缺的人行连廊它更全。但 10 万对要发 10 万次请求，只适合当**第三方复核** |
| 本地自建 OSM 路网 | ✅ | 无限制 | ❌ | 需 1–2 h 建图 | 最彻底、完全可复现，但为了 10 万对不值得 |

**为什么有 key 也用不了（额度算一遍就清楚了）：**

| 服务 | 计费单位 | 跑完 101,423 对需要 | 免费额度 | 结论 |
|---|---|---|---|---|
| GraphHopper Matrix | `起点×终点 ÷ 2` credits | 约 **82,500** credits | 500 / 天 | 约 165 天 |
| GraphHopper 逐对 | 1 credit / 对（最低 1） | **101,423** credits | 500 / 天 | 约 203 天 |
| OneMap | 1 请求 / 对，无矩阵 | **101,423** 次请求 | 未公布，429 即超额 | 不可行 |

反过来算，免费额度足够做**抽检**：GraphHopper 500 credits/天 = 500 对，OneMap 即使保守按 1 req/s 算，一夜也能复核几千对。所以正确的用法不是「换个服务跑全量」，而是**用它们验证 OSRM 算得对不对**。


### 3.3 关键工程细节

**① 先用直线距离剪枝，但剪枝半径必须保守。**
路网距离 ≥ 直线距离，所以直线 > 3000 m 的对一定不可能在 3000 m 步行范围内，可以直接丢掉。这样 12442 × 129 = 160 万对缩到 **101,423** 对（每套房平均 8.15 个候选）。剪枝半径与步行上限取同一个值（3000 m）不是巧合：因为 `walk ≥ straight`，`walk ≤ 3000` 必然蕴含 `straight ≤ 3000`，所以这个半径能保证**步行 3000 m 以内的对一条不缺**；半径再小会漏数据，再大则算出来也不会被保留。

**② 用 `/table` 而不是 `/route`。**
`/route` 一次只能算一对，要发 10 万次。`/table` 一次返回 S×D 矩阵。把空间上相邻的 75 个 block 打包成一批，目标集取这批 block 候选的并集（平均 10 个），一次请求出 75×10 的结果。

**③ 加上 snap 偏移。**
OSRM 会把坐标吸附到最近的路网节点，返回的 `distance` 只是节点到节点的距离。要把它加回去才是「门到门」距离：

```
walk_distance = network_distance + snap(起点) + snap(终点)
```

**④ 限速。** demo 服务器明确要求 ≤ 1 req/s。第一版用了 0.15 s 间隔，直接吃 **HTTP 429**，然后指数退避重试，看起来「很慢」其实是在被限流。改成 2.0 s 间隔后 0 次 429。

---

## 4. 三级 Proximity 定义

> 下面这段可以直接贴进报告的 Task 1 部分。

**English (ready to paste):**

> **Proximity definition.** We define the proximity between an HDB block and a hawker centre as the **walking path length** along the OpenStreetMap pedestrian network, obtained from an OSRM `foot`-profile routing service and corrected for the endpoint snapping offset (i.e. true door-to-door distance). We deliberately do not use great-circle distance, because in Singapore a straight line routinely crosses HDB blocks, canals, expressways and MRT viaducts: the median detour factor of the walking network is **1.44×** the straight line (p5 1.25×, p95 2.97×, and 18.7% of blocks exceed 2×). Crucially, this ratio is not a constant that could be corrected away — even after re-calibrating the straight-line thresholds to their best possible values (268 m and 645 m), **15.4% of blocks still land in the wrong class**. Three proximity classes are defined on the walking distance *d*:
>
> | Class | Label | Walking distance | Equivalent walking time |
> |---|---|---|---|
> | 1 | very near | *d* ≤ 500 m | ≈ 7 min at 4.5 km/h |
> | 2 | near | 500 m < *d* ≤ 1000 m | ≈ 7–14 min |
> | 3 | far | *d* > 1000 m | > 14 min |
>
> 500 m is the conventional walking-catchment radius for a neighbourhood amenity and 1000 m is the "10-minute walk" boundary; both thresholds are chosen a priori from planning practice rather than fitted to the data. Because proximity is a many-to-many relationship (one block reaches several hawker centres), we model it as the relation `hawker_proximity(block_id, hawker_id, walk_distance_m, proximity_class)`. A block's *own* proximity class, as used in Q4, is the minimum class over all hawker centres it can reach, i.e. the class of its nearest centre.

**为什么是 500 / 1000 而不是别的：**
- 500 m 是城市规划里社区设施的常规步行半径（HDB 的 neighbourhood centre 服务半径大致就是这个量级）
- 1000 m ≈ 10 分钟步行，是「步行可达」的通行上限
- 两个阈值都是**先验**定的，不是照着数据分布切出来的——这一点在 viva 上很重要，否则会被问「你是不是为了让结果好看才这么切的」

---

## 5. 实测结果

### 5.1 规模

| 项目 | 数量 |
|---|---|
| HDB block（去重后的起点） | 12,442（其中 **10,181** 是有住宅单位的，2,261 是纯商业楼） |
| Hawker centre（终点） | 129 |
| 直线距离剪枝后的候选对 | 101,423（每套房平均 8.15 个） |
| 实际发出的 HTTP 请求 | 220 次，约 37 分钟 |
| `hawker_proximity` 表行数 | **63,340**（≤3000 m 步行 63,237 + 103 条兜底） |
| 覆盖率 | **12,442 / 12,442**（100%，每套房至少 1 个可达 hawker） |
| 关系的完整性 | **≤3000 m 步行范围内一条不缺**（论证见 §5.6） |

### 5.2 三级分类结果

**只统计 10,181 个有住宅单位的 block**（这才是会出现在 resale 交易里的集合）：

| Class | 定义 | Block 数 | 占比 |
|---|---|---|---|
| 1 · very near | walk ≤ 500 m | 1,509 | **14.8%** |
| 2 · near | 500–1000 m | 3,243 | **31.9%** |
| 3 · far | walk > 1000 m | 5,429 | **53.3%** |

三类都有足够的样本，**Q4 拿 class 1 和 class 3 对比不会遇到空组**。

### 5.3 直线距离到底差多少

| 指标 | 值 |
|---|---|
| 步行 / 直线 中位数 | **1.44×**（均值 1.56×） |
| 步行 / 曼哈顿 中位数 | **1.15×**（均值 1.24×） |
| 「最近那个 hawker」的绕行系数 p5 / p50 / p95 | **1.25 / 1.55 / 2.97** |
| 绕行系数 > 2.0× 的 block | 2,315 个（**18.7%**） |
| 步行 > 1.5× 直线的 pair | 24,515 个（38.8%） |

**关键结论（viva 会被问的点）：**

如果直接把 500/1000 m 阈值套到直线距离上，**49.2% 的 block 会被分错类**——但这个比较不公平，因为直线距离系统性偏短。

公平的问法是：**给直线距离重新标定一对最优阈值，能不能救回来？** 答案是**不能**：

| 方法 | 最优阈值 | 分类一致率 | 仍然分错 |
|---|---|---|---|
| 步行距离（我们的方案） | 500 / 1000 m | — | — |
| 直线距离 + 最优重标定 | 268 / 645 m | 84.6% | **15.4%** |
| 曼哈顿距离 + 最优重标定 | 321 / 781 m | 83.5% | **16.5%** |

也就是说：**即使你把直线距离的阈值调到最优，每 6 套房里还是有 1 套会被分到错的 proximity class。** 原因是绕行系数不是常数——p5 是 1.25、p95 是 2.97，任何单一的修正系数都会在尾部失效。这正好证明了作业里那句 "may require additional data or API calls" 的必要性。

另外还有 **10.0% 的 block**，用直线距离找出来的「最近 hawker」跟实际步行最近的**根本不是同一个**。

### 5.4 交叉验证（三个独立路由引擎）

我们用了三个**互不相关**的步行路由服务复核同一批距离，这一点在 viva 上很有用——它把「距离算得对不对」从「相信某个库」变成「三个来源互相印证」。

**① OSRM vs Valhalla（同为 OSM 系，但实现完全独立）** — 30 对：

| 指标 | 值 |
|---|---|
| 中位绝对相对误差 | **5.69%** |
| 平均绝对相对误差 | 10.40% |
| 有符号误差中位数 | **+3.20%**（OSRM 系统性偏长约 3%） |
| 误差 ≤ 5% 的比例 | 13 / 30（43%） |
| 误差 ≤ 10% 的比例 | 23 / 30（77%） |

**② OSRM vs OneMap（新加坡土地管理局 SLA 的官方路网，完全不同的数据来源）** — 299 对，分层抽样覆盖 0–3000 m：

| 指标 | 值 |
|---|---|
| 中位绝对相对误差 | 9.70% |
| 有符号误差中位数 | **+9.18%**（OSRM 偏长） |
| OneMap 更短的对数 | **254 / 299（85%）**——是系统性偏差，不是噪声 |
| 误差 ≤ 5% / ≤ 10% / ≤ 15% | 27% / 52% / 66% |
| **扣掉 snap 偏移后**的中位误差 | **+6.05%**（均值 11.50%） |

最后一行是关键：我们的 `walk_distance_m` 是**门到门**距离（含平均 46 m 的吸附偏移），而 OneMap 只报**路网距离**。把偏移扣掉之后，两者中位差从 9.2% 降到 6.1%——说明这 3 个百分点纯粹是**定义差异**，不是算法错误。

**③ 但 OneMap 暴露了一个我们自己的问题（必须写进 caveat）**

误差不是均匀分布的，它集中在**短距离**：

| OSRM 步行距离 | 对数 | 中位误差（门到门） | 中位误差（扣 snap） |
|---|---|---|---|
| < 500 m | 15 | **+30.8%** | +12.4% |
| 500–1000 m | 26 | +10.3% | +3.3% |
| 1000–2000 m | 106 | +10.6% | +7.8% |
| > 2000 m | 152 | +7.2% | +5.4% |

极端个案离谱到不像同一个世界：

| 对 | 直线 | OSRM | OneMap | OSRM/直线 |
|---|---|---|---|---|
| 104 Jln Bt Merah → Blk 112 Jln Bt Merah | 206 m | 1,022 m | 266 m | **4.95×** |
| 613 Bedok Reservoir Rd → Blk 630 | 330 m | 1,268 m | 426 m | 3.84× |
| 12 Holland Ave → Holland Village | 191 m | 698 m | 324 m | 3.65× |

直线 206 m、两家店在同一条路上，OSRM 却绕了 1 km——这几乎肯定是 OSRM 把起点吸附到了**道路另一侧**（快速路/水渠对面），而 OneMap 走了正确的人行连廊。全量数据里绕行系数 > 3× 的有 **699 对（1.1%）**，其中 **424 个 block 的「最近 hawker」落在这批可疑对里**。

**影响有多大：已经从抽样补全走到全量重算**

最初只重算了 424 个可疑 block 涉及的 10,249 对，但那是**按「距边界 ±25%」选的**，等于专挑边界、冷落近端——后来一查覆盖度，最要紧的 class 1（<500 m）反而只有 43.1%。于是先补到 27,208 对（class 1/2 与 ≤1500 m 全覆盖），最终把**全部 63,340 对**都跑完了：

| 覆盖范围 | 对数 | 覆盖率 |
|---|---|---|
| class 1（<500 m） | 2,289 | **100%** |
| class 2（500–1000 m） | 6,569 | **100%** |
| ≤1500 m 全部 | 18,889 | **100%** |
| class 3（>1000 m） | 54,482 / 54,482 | **100%** |
| 合计 | 63,340 / 63,340 | **100%** |

中途停在 1500 m 的理由曾经是：class 1/2 的边界是 500 / 1000 m，**每一个可能属于 class 1 或 2 的对都已在表内**；1000–1500 m 这一带之所以也要，是因为 OneMap 中位比 OSRM 短 5%，一个 1,300 m 的对有可能落到 1000 m 以内翻进 class 2；再往外的对要翻类需要 >33% 的缩短，已属尾部（实测比值 p1 = 0.41）。全量跑完之后，这个残余风险不再需要作为假设保留——**63,340 对全部有 OneMap 实测值，审计覆盖 100%**。

| 指标 | 值 |
|---|---|
| 重算对数 | **63,340 / 63,340**，全部成功，0 条无路线 |
| OneMap vs OSRM（门到门） | 中位 **−4.6%**，p10 −20.2%，p90 +5.8% |
| 差异 > 25% 的对 | **5,024（7.9%）** |
| OneMap 更短 / 更长 / 相等 | 46,570 / 16,698 / 72 |

（口径说明：早期基于 27,208 对子集的数字是中位 −5.0%、>25% 占 14.1%——那个子集专挑边界附近，样本偏斜；上表为全量 63,340 对口径，引用时用这一版。）

**block 级影响（全部 12,442 个 block——全量覆盖后每个 block 的 OneMap 最近中心都是实测值，不再是估计）**

| 指标 | 值 |
|---|---|
| 改用 OneMap 后跨类的 block | **2,186 / 12,442（17.6%）** |
| 类别分布（class 1 / 2 / 3） | 2,007 / 3,907 / 6,528 → **2,728 / 4,143 / 5,571** |
| 变近 / 变远 | 9,033 / 3,346（63 个不变） |

全量口径下方向依然不含糊：**9,033 个 block 变近、3,346 个变远**，跨类的 2,186 个里 1,898 个是变近；OneMap 把 class 1 的 block 从 2,007 拉到 2,728（**+35.9%**）。只看会进入 resale 交易的 10,181 个住宅 block：跨类 1,850（18.2%），类别分布 1,509 / 3,243 / 5,429 → 2,105 / 3,467 / 4,609。

（历史口径备注：本节早期版本只覆盖 27,208 对，分母是「最近中心 ≤1500 m」的 9,166 个 block，报 23.2% 跨类——那个分母剔除了 3,276 个够不着边界的 class 3 block。现在全量覆盖，分母就是全部 12,442 个 block，率降到 17.6% 纯粹是因为分母变大了，受影响 block 的绝对数只增不减。）

**这个偏移必须在报告里说清楚**：Q5 比较的是「class 1 vs class 3」的中位价，而 class 1 的数量对引擎敏感。我们的处理是**主结论只用 OSRM、并把 OneMap 的结果作为敏感性检验**，而不是挑一个好看的报。

**我们怎么处理：OSRM 作主，OneMap 作审计**

主表 `hdb_hawker_proximity.csv` 仍然**只用 OSRM**，理由只有一条：OSRM 一次请求能算 100 个坐标，所以能覆盖全部 63,340 对；OneMap 没有批量接口，单 token 实测稳态只有 **1.4 req/s**，全量要 8 小时以上。混用两个引擎会让"为什么这一行是 OneMap、那一行是 OSRM"变得没法解释，而这个代价不值得。

但差异不隐藏：

- `data/hawker_geo/onemap_distances.csv` —— 63,340 对的逐对对比（`osrm_m` / `onemap_raw_m` / `onemap_m` / `status`），可以直接拿去画误差分布；
- `data/hawker_geo/onemap_full.jsonl` —— 原始缓存，有了它 `07 --merge-only` 几秒就能重建合并表，不用再发一次请求；
- 想整表切到 OneMap 只需一行：`03_derive_proximity.py --walk data/hawker_geo/hdb_hawker_walk_onemap.csv`。

**这个重算反而加强了 §5.3 的论点**：用 OneMap 距离时，直线近似的最优误分类率是 **9.0%**（阈值 309 / 698 m，全量 63,340 对口径），用 OSRM 是 **15.4%**（268 / 645 m）。两个数都远高于"可以忽略"，所以"必须走真实路网"这个结论**不依赖于选哪个引擎**——这是比单一引擎自证更有力的证据。


### 5.5 一个意外的发现（可以直接写进 Q4/Q5 的 insight）

按 town 汇总「最近 hawker 的步行距离中位数」，结果非常符合现实：

| 城镇 | 中位步行距离 | 城镇 | 中位步行距离 |
|---|---|---|---|
| CT（Chinatown） | 368 m | BD（Bedok） | 886 m |
| MP（Marine Parade） | 456 m | BP（Bukit Panjang） | 1,021 m |
| GL（Geylang） | 462 m | SK（Sengkang） | 1,018 m |
| BM（Bukit Merah） | 488 m | HG（Hougang） | 1,094 m |
| QT（Queenstown） | 539 m | WL（Woodlands） | 1,151 m |
| AMK（Ang Mo Kio） | 545 m | YS（Yishun） | 1,164 m |
| TP（Toa Payoh） | 651 m | TAP（Tampines） | 1,424 m |
| JW（Jurong West） | 870 m | **PG（Punggol）** | **1,525 m** |
| | | **SGN（Sengkang New）** | **1,560 m** |
| | | **PRC（Punggol Coast）** | **1,706 m** |

老城区（Chinatown、Marine Parade、Geylang、Bukit Merah）步行 400–500 m 就能到 hawker centre，新镇（Punggol、Sengkang、Tampines）要 1.5 km 以上。这跟「新镇 hawker centre 供给不足」的现实认知一致——**这本身就是 Q4 可以引用的一个真实 insight**，而且反过来证明我们的距离算对了：如果距离算错了，不会这么整齐地呈现出这个地理规律。

---

### 5.6 完整性：到底有没有缺数据

分三层看，结论不一样。

**第一层：已经路由的 pair，一条不缺。**
220/220 批次全部完成，101,423/101,423 条结果，0 条不可达、0 条重复。12,442 个 block 全部至少 1 个 hawker，129 家 hawker 全部被引用到。

**第二层：≤3000 m 步行范围内，数学上可以证明完整。**
剪枝用的是 3000 m **直线**距离。因为路网距离 ≥ 直线距离，所以任何步行 ≤3000 m 的 pair，其直线距离必然 ≤3000 m，必然已经被路由。**因此表里所有步行 ≤3000 m 的 pair 一条不缺。** 这也正是把上限设成 3000 而不是更大的原因——超过 3000 拿到的就不是完整集合，而是一个参差不齐的子集。

**第三层：真正的缺口在源头，不在距离。**
Q5 需要"有 fresh market stalls 的 hawker"。有 **4,320 个 block（34.7%）** 在 3 km 步行范围内找不到任何**已知**带菜市场的中心，原因分两种：

| 原因 | Block 数 | 性质 |
|---|---|---|
| 3 km 内只有那 23 家"属性未知"的中心 | 4,217 | **源数据缺口**，补不了 |
| 3 km 内一家中心都没有（最近的在 3000–3506 m） | 103 | **我们主动排除**（超过 40 分钟步行，不算 proximity） |

那 4,217 个 block 的最近中心**全部**是那 23 家"属性未知"的新中心——没有一家是"已知没有菜市场"的。按城镇看高度集中（括号内为该镇 block 总数）：

| 城镇 | 缺席 block | 城镇 | 缺席 block |
|---|---|---|---|
| SK Sengkang | 835 / 835 | BP Bukit Panjang | 417 / 417 |
| PG Punggol | 670 / 670 | WL Woodlands | 414 / 874 |
| CCK Choa Chu Kang | 639 / 639 | PRC Pasir Ris | 536 / 543 |

**根因**：官方数据集的元数据写着 `coverageEnd = 2016-09-01`，而且只收录 `owner ∈ {HDB(82), Government(25)}` 的中心。2016 年之后由 NEA 委任经营者运营的新中心，既不在时间范围内、也不在产权范围内——所以它们的摊位数据**在任何免费公开数据源里都不存在**。我们查过 data.gov.sg 的 v2 API 和 OneMap，都没有更完整的版本。

**这不是 bug，是必须写进报告的限制。** Q5 建议这样处理：

1. 用 `no_of_mkt_produce_stalls > 0` 过滤（NULL 自动排除），并在报告里注明"Q5 的样本偏向成熟组屋区，新镇（Punggol / Sengkang）系统性缺失"；
2. 或者把 Q5 的结论限定为"在有已知菜市场的中心附近"，并把 65.4% 的覆盖率作为 caveat 写出来。

**这个偏差本身也是一个 insight**：新镇不但 hawker 距离更远（§5.5），连数据覆盖也更差——两个偏差方向一致，会**叠加**放大 Q5 里新镇与老城的差异。写结论时必须说清楚这一点，否则容易被质疑。

---

### 5.7 接上 resale 交易表：Q4/Q5 从"算不出"到"算得出"

在此之前我们这块是**断链**的：`hdb_block.block_id` 是我们自己合成的 `B00001`，而 resale 文件里只有 `block` + `street_name` 文本。没有这个映射，Q4/Q5 一行都跑不出来。

数据来自组内仓库 `SD6103_Project/data/src/resale/`（`RESALE_CLEAN.py` 清洗，986,720 行，1990-01 至 2026-09，51 行剔除记录留在 `RESALE_REMOVED.csv`）。连接由 `08_join_resale.py` 完成。

**匹配策略：先精确，再兜底，绝不猜。**

1. 归一化 `(block, street_name)` 与 `(blk_no, street)`：大写、去 `BLK` 前缀、去标点、合并空格；
2. 精确命中；
3. 未命中再展开路型缩写（`AVE→AVENUE`、`STH→SOUTH`、`BT→BUKIT` 等）重试一次；
4. 仍不命中就**留空**，不靠模糊匹配硬凑——错配比不配更糟。

| 指标 | 数值 |
|---|---|
| 交易总数 | 986,720 |
| 精确匹配 | 973,782（**98.69%**） |
| 缩写展开后额外命中 | 0 |
| 未匹配（`block_id = NULL`） | 12,938（1.31%），涉及 172 个 block |
| 城镇交叉校验冲突 | **0** |

**为什么那 1.31% 匹配不上——不是拼写问题，是楼已经拆了。**
172 个未匹配 block 里，12,937 行的首次成交年份落在 1990 年代（如 `33 TAMAN HO SWEE`、`403 CLEMENTI AVE 1`、`168 BOON LAY DR`）。这些 block 在现行的 HDB 物业登记册里**根本不存在**，因为已经拆除重建（SERS），自然也没有坐标、没有 proximity class。所以 `block_id` 设计为**可空**：交易记录保留（交易总数才不会说谎），内连接 `hdb_block` 时自动排除。

只有一个例外值得单独记一笔：`464B CLEMENTI AVE 1`，2026 年成交 1 行，而登记册里连 464A/464C 都没有——这是**新落成、登记册快照还没收录**的楼，方向与拆迁相反。

另有一个城镇 `LIM CHU KANG`（64 行）不在 26 个 HDB 市镇里，是农场区，本来就没有组屋 block。

**注意两套城镇口径。** `hdb_block.town` 存的是 HDB 的三字母代码（`AMK` / `CT` / `KWN` / `SGN` …，源文件就是这么给的），而 resale 文件写的是全名（`ANG MO KIO` / `CENTRAL AREA`）。两边都是 26 个取值、一一对应，映射唯一（已验证：没有任何代码对应两个名字），但**不能直接拿文本 join**——`'CT' = 'CENTRAL AREA'` 永远是 false。所以 Q4 按 block 的代码分组，再经 `town_name` 视图翻译成全名输出。要根治得加一张 town 维表、两边都挂外键，那属于主 schema 的改动，留给组里定。

**验证结果**（4 房，2020 年以后，避免户型与楼龄混淆）：

- **Q4**：三类齐全且每类 ≥30 笔的城镇 **21 / 26**（完整 21 行结果见 `09_queries_hawker.sql` 注释）。被刷掉的 5 个城镇分两种性质，报告里要分开写：4 个是**结构性的**——BT、MP 没有 class-3 房源，PG 没有 class-1，SGN 只有 class-3，这是地理事实、不是缺数据；只有 **CT 中区**是**样本太薄**（n1=440、n2=80，但 n3 只有 10 笔），是"远离小贩中心的房子确实存在、但少到算不出中位数"的唯一例子；
- **Q5**：1 km 内有已知菜市场中心的 4 房，中位价 S$615,000；没有的 S$536,000，**高 14.7%**。若把 23 家属性未知的中心视为"有菜市场"，差距收窄到 **+5.0%**（新镇被并入"有"这一组，把均值拉低）——这个跨度必须在报告里说明，它是口径差异，不是数据错误。

---

## 6. 交付物清单

代码在 `scripts/hawker_geo/`，数据在 `data/`，跟仓库其它部分一致。

```
scripts/hawker_geo/
├── 01_clean_hawker_geo.py        清洗 + join 官方数据集
├── 02_compute_walk_distance.py   OSRM 步行距离（可断点续跑）
├── 03_derive_proximity.py        分级 + 与直线/曼哈顿的对比（含最优阈值搜索）
├── 04_schema_hawker.sql          建表 DDL（含 CHECK 约束）
├── 05_validate_osrm.py           用 Valhalla 交叉验证距离
├── 06_validate_onemap.py         用 OneMap（SLA 官方路网）交叉验证距离
├── 07_reroute_onemap.py          用 OneMap 全量重算 63,340 对（可断点续跑，--merge-only 秒级重建）
├── 08_join_resale.py             resale 交易 → block_id 连接（Q4/Q5 的前提）
├── 09_queries_hawker.sql         Q4 / Q5 正式查询（含敏感性分析 + 覆盖率诊断）
└── _verify_queries.py            不连数据库时用 Python 复算 Q4/Q5，核对 SQL 注释里的数字

data/raw_data/                    （输入，已存在于仓库）
├── hdb.csv                       siteselect_sg 原始文件（12,442 个 block）
├── hawker_geo.csv                129 家小贩中心的原始地理数据
└── hawker_official_raw.json      data.gov.sg 官方数据集缓存（107 行）

data/src/resale/                  （输入，已存在于仓库）
├── RESALE_CLEANED.csv            组内清洗后的交易表（986,720 行）
└── RESALE_REMOVED.csv            被剔除的 51 行 + 原因

data/hawker_geo/                  （本工作流产出）
├── hawker_centre.csv               129 行 → hawker_centre 表
├── hawker_centre_match_report.csv  join 审计（每行匹配方式）
├── hdb_block.csv                   12,442 行 → hdb_block 表
├── hdb_hawker_walk.csv             原始步行距离对（63,340 行，可由缓存秒级重建）
├── hdb_hawker_proximity.csv        → hawker_proximity 表（63,340 行，带 class）
├── hdb_block_nearest.csv           每套房的最近 hawker 与 class（Q4 用）
├── resale_transaction.csv          → resale_transaction 表（986,720 行，含 NULL block_id）
├── resale_unmatched.csv            172 个未匹配 block + 行数 + 年份区间
├── resale_join_report.txt          连接审计报告
├── method_comparison.csv           直线/曼哈顿/步行 三种算法的分类差异
├── router_validation.csv           OSRM vs Valhalla 的 30 对复核
├── onemap_validation.csv           OSRM vs OneMap 的 299 对复核
├── onemap_distances.csv            全量 63,340 对的 OSRM vs OneMap 逐对对比（审计主产物）
├── onemap_full.jsonl               OneMap 原始缓存（提交进仓库，断点续跑靠它）
└── osrm_cache.jsonl                路由缓存（提交进仓库，省掉 37 分钟重跑）
```

`hdb_hawker_walk.csv`、`hdb_hawker_walk_onemap.csv` 和 `resale_transaction.csv` 没进仓库，见根目录 `.gitignore`：三者都能从已提交的缓存在几秒到一分钟内重算（OneMap 替代表用 `07_reroute_onemap.py --merge-only` 重建）。

## 7. 复现方式

```bash
# 在仓库根目录执行
python scripts/hawker_geo/01_clean_hawker_geo.py        # 需联网一次，之后走缓存
python scripts/hawker_geo/02_compute_walk_distance.py   # 有缓存时几秒；无缓存 220 次请求、约 37 分钟
python scripts/hawker_geo/03_derive_proximity.py
python scripts/hawker_geo/05_validate_osrm.py           # 可选：Valhalla 复核，约 1 分钟
export ONEMAP_TOKEN='...'                               # OneMap token（72 小时过期）
python scripts/hawker_geo/06_validate_onemap.py         # 可选：OneMap 复核，300 对约 2 分钟
python scripts/hawker_geo/07_reroute_onemap.py --merge-only   # 从 onemap_full.jsonl 重建全量对比表，几秒
python scripts/hawker_geo/08_join_resale.py             # resale → block_id，约 1 分钟
psql -f scripts/hawker_geo/04_schema_hawker.sql         # 建表 + \copy 装载 + 行数校验
psql -f scripts/hawker_geo/09_queries_hawker.sql        # Q4 / Q5，注释里带已核实的结果
```

`04_schema_hawker.sql` 里的 `\copy` 路径是相对仓库根目录写的，所以 `psql` 也要从根目录跑。两张带"多余列"的 CSV（`hawker_centre.csv`、`hdb_hawker_proximity.csv`）先落到 `UNLOGGED` 暂存表再投影进正式表，所以不会因为导出文件多带了 provenance 列而报错。装载后脚本末尾会打印四张表的行数，应分别为 12,442 / 129 / 63,340 / 986,720。

`09_queries_hawker.sql` 每条查询下面都附了**已核实的结果**，方便组员判断自己那次跑得对不对。这些数字是在没有 PostgreSQL 服务的环境里核对的：DDL 和查询用 `pglast` 离线解析通过（18 + 5 条语句），结果集则由 `_verify_queries.py` 在 CSV 上用纯 Python 独立复算（`PERCENTILE_CONT(0.5)` 的线性插值与 `statistics.median()` 等价）。所以如果你本地跑出来的数不一样，先怀疑装载，再怀疑查询。

`02` 支持断点续跑：每完成一批就写一行 `data/hawker_geo/osrm_cache.jsonl`，重跑会自动跳过已完成的批次（`[cache] 220 groups already computed` → `[osrm] 0 requests to run`，只重新拼装输出，几秒钟）。要改分级阈值直接 `python scripts/hawker_geo/03_derive_proximity.py --class1 400 --class2 800`，**不需要重算距离**。
