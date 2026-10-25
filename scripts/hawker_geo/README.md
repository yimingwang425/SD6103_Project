# Hawker-Geo 工作流

Data Systems Project · AY26S1 · 负责范围：hawker centre 地理数据 + HDB↔Hawker 步行距离

## 产出

`schema_hawker.sql` 按团队逻辑模型（`logical_schema.drawio`）建 7 张表（`TOWN`、`HDB_BLOCK`、`HDB_FLAT`、`HAWKER_CENTRE`、`PROXIMITY_LEVEL`、`PROXIMITY`、`RESALE_TRANSACTION`）加 1 个视图；`queries_hawker.sql` 是 Q4/Q5 查询，每条查询下面附已核实的结果，跑完可以直接对数字。

## Proximity 定义

| Level | 步行距离 |
|---|---|
| 1 NEAR | ≤ 500 m |
| 2 MID | 500–1000 m |
| 3 FAR | > 1000 m |

距离是 OSRM `foot` 路网算出来的**步行路径长度**（加了端点吸附偏移，门到门），不是直线距离。直线距离在新加坡会穿组屋、水渠和快速路：绕行系数中位数 1.44×，而且不是常数——即使给直线距离重新标定最优阈值（268/645 m），仍有 **15.4%** 的 block 分错级，所以必须走真实路网。`PROXIMITY_LEVEL` 的 band 记法是 `(min, max]`：边界值归低档（数据里恰好有一对 1000.0 m，判 level 2）。一个 block 自己的 level = 它最近那个 hawker 的 level（Q4 用）。

## 流程

| 脚本 | 作用 |
|---|---|
| `01_clean_hawker_geo.py` | 清洗 hawker_geo.csv，join 官方数据集拿摊位数据 → `hawker_centre.csv` |
| `02_compute_walk_distance.py` | OSRM 步行距离，可断点续跑 → `town.csv`、`hdb_block.csv`、`hdb_hawker_walk.csv` |
| `03_derive_proximity.py` | 分级 + 三种距离指标对比 + resale 连接 → `proximity_level.csv`、`hdb_hawker_proximity.csv`、`hdb_flat.csv`、`resale_transaction.csv` |
| `04_validate_distances.py` | 用 Valhalla / OneMap 交叉验证距离（可选） |
| `05_reroute_onemap.py` | 用 OneMap 全量重算 + 逐对对比表（可选，已跑完） |
| `verify_queries.py` | 不连数据库，用 Python 复算 Q4/Q5 核对注释里的数字 |

## 复现

```bash
# 在仓库根目录执行
python scripts/hawker_geo/01_clean_hawker_geo.py        # 有缓存，几秒
python scripts/hawker_geo/02_compute_walk_distance.py   # 有缓存几秒；无缓存 220 次请求约 37 分钟
python scripts/hawker_geo/03_derive_proximity.py        # 表数据全产出（含 resale 连接，约 1 分钟）

# 建表后装载：CSV 的列与表完全一致、顺序相同，直接 copy，不需要暂存表
psql -U postgres -d sd6103_project -f scripts/hawker_geo/schema_hawker.sql
psql -U postgres -d sd6103_project <<'SQL'
\copy TOWN               FROM 'data/hawker_geo/town.csv'                WITH (FORMAT csv, HEADER true)
\copy HDB_BLOCK          FROM 'data/hawker_geo/hdb_block.csv'           WITH (FORMAT csv, HEADER true)
\copy HDB_FLAT           FROM 'data/hawker_geo/hdb_flat.csv'            WITH (FORMAT csv, HEADER true)
\copy HAWKER_CENTRE      FROM 'data/hawker_geo/hawker_centre.csv'       WITH (FORMAT csv, HEADER true)
\copy PROXIMITY_LEVEL    FROM 'data/hawker_geo/proximity_level.csv'     WITH (FORMAT csv, HEADER true)
\copy PROXIMITY          FROM 'data/hawker_geo/hdb_hawker_proximity.csv' WITH (FORMAT csv, HEADER true)
\copy RESALE_TRANSACTION FROM 'data/hawker_geo/resale_transaction.csv'  WITH (FORMAT csv, HEADER true)
SQL

# 行数应为 26 / 12,442 / 145,465 / 129 / 3 / 63,340 / 986,720
for t in TOWN HDB_BLOCK HDB_FLAT HAWKER_CENTRE PROXIMITY_LEVEL PROXIMITY RESALE_TRANSACTION; do
  psql -U postgres -d sd6103_project -tAc "SELECT COUNT(*) FROM $t"
done

psql -U postgres -d sd6103_project -f scripts/hawker_geo/queries_hawker.sql
```

## 与逻辑模型的对齐

- id 全是 INT 代理键，由脚本按确定性规则分配：`town_id` 用队友 `hdb_town_YM.csv` 的 TownID；`block_id` = HDB register 行号；`hawker_id` = geo 文件行号；`flat_id` / `transaction_id` = resale 文件行序。路由缓存内部仍用 `B00001`/`H001` 旧编号，03 在出口处映射成 INT。
- 两处有意的偏离：① 列名是 `remaining_lease_years` 而不是图上的 `remaining_lease_months`——上游 `RESALE_CLEAN.py` 把「X 年 Y 个月」的月份部分丢了，仓库里没有原始 resale 文件，无法精确恢复（上游那个正则本来就抓到了月份，要补很容易）。② `transaction_month` 存成 `DATE`，取值月初 1 号，因为源数据只到月。
- `HDB_FLAT` 的身份是五元组（block, flat_type, storey_range, floor_area_sqm, flat_model）：resale 数据没有单元号，五个字段全同的单元会合并成一行。
- `PROXIMITY` 上有一个 CHECK 把 `level_id` 和 `distance_m` 绑在一起（图上没有）：这个项目早期就因为直线阈值和查询假设不一致出过事，约束让这种漂移装不进库。
- `flat_model` 是 VARCHAR(40) 而不是图上的 20：数据里最长的 "Premium Apartment Loft" 要 22 字符。

## 数据

输入（已在仓库）：`data/raw_data/hdb.csv`（12,442 个 block）、`data/raw_data/hawker_geo.csv`（129 家中心）、`data/raw_data/hawker_official_raw.json`（官方数据集缓存）、`data/src/resale/RESALE_CLEANED.csv`（986,720 行交易）。

产出（`data/hawker_geo/`）：`town.csv`、`hawker_centre.csv`、`hawker_centre_match_report.csv`（join 审计）、`hdb_block.csv`、`proximity_level.csv`、`hdb_hawker_proximity.csv`、`hdb_flat.csv`、`resale_transaction.csv`、`hdb_block_nearest.csv`、`resale_unmatched.csv`、`resale_join_report.txt`、`method_comparison.csv`、`router_validation.csv`、`onemap_validation.csv`、`onemap_distances.csv`、`onemap_full.jsonl`、`osrm_cache.jsonl`。

`hdb_hawker_walk.csv`、`hdb_flat.csv` 和 `resale_transaction.csv` 没进仓库（见根目录 `.gitignore`），都能从已提交的缓存在几秒到一分钟内重算。

## 关键数字

- 12,442 个 block × 129 家中心 → 直线 3000 m 剪枝后 101,423 个候选对 → **63,340** 对进入关系（63,237 对在 3000 m 内 + 103 个 block 的最近中心兜底），每个 block 至少 1 个可达中心（100% 覆盖）。剪枝半径与步行上限同为 3000 m，所以「步行 3000 m 以内一条不缺」是可证明的。
- 三级分布（10,181 个住宅 block）：level 1 有 1,509、level 2 有 3,243、level 3 有 5,429，三级样本都足够，Q4 不会遇到空组。
- 交叉验证：OSRM vs OneMap（全量 63,340 对）中位差 −4.6%，7.9% 的对差 >25%；17.6% 的 block 会跨级。主表仍用 OSRM（唯一有批量接口、能全覆盖的引擎），OneMap 结果作敏感性检验，两边数字都在报告里给。
- Q4：21/26 个城镇三级齐全；结果非单调（level 2 常比 level 1 便宜），proximity 是弱信号、更像「组屋成熟度」的代理。
- Q5：1 km 内有已知菜市场的 4 房中位价 S$615,000，没有的 S$536,000（+14.7%）；把 23 家属性未知的中心算作「有」则收窄到 +5.0%。两个数都要报。
- 34.7% 的 block 在 3 km 内找不到**已知**带菜市场的中心——官方数据集 2016 年快照不收新镇的新中心，这是源数据缺口，报告里要写清楚（新镇同时距离更远、覆盖更差，两个偏差方向一致会叠加）。
