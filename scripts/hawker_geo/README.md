# Hawker-Geo 工作流

Data Systems Project · AY26S1 · 负责范围：hawker centre 地理数据 + HDB↔Hawker 步行距离

## 产出

`schema_hawker.sql` 建 4 张表（`hdb_block`、`hawker_centre`、`hawker_proximity`、`resale_transaction`）加 2 个视图；`queries_hawker.sql` 是 Q4/Q5 查询，每条查询下面附已核实的结果，跑完可以直接对数字。

## Proximity 定义

| Class | 步行距离 |
|---|---|
| 1 | ≤ 500 m |
| 2 | 500–1000 m |
| 3 | > 1000 m |

距离是 OSRM `foot` 路网算出来的**步行路径长度**（加了端点吸附偏移，门到门），不是直线距离。直线距离在新加坡会穿组屋、水渠和快速路：绕行系数中位数 1.44×，而且不是常数——即使给直线距离重新标定最优阈值（268/645 m），仍有 **15.4%** 的 block 分错类，所以必须走真实路网。一套房自己的 class = 它最近那个 hawker 的 class（Q4 用）。

## 流程

| 脚本 | 作用 |
|---|---|
| `01_clean_hawker_geo.py` | 清洗 hawker_geo.csv，join 官方数据集拿摊位数据 → `hawker_centre.csv` |
| `02_compute_walk_distance.py` | OSRM 步行距离，可断点续跑 → `hdb_hawker_walk.csv`、`hdb_block.csv` |
| `03_derive_proximity.py` | 分级 + 三种距离指标对比 + resale 连接 block_id → 全部表数据 |
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
\copy hdb_block          FROM 'data/hawker_geo/hdb_block.csv'           WITH (FORMAT csv, HEADER true)
\copy hawker_centre      FROM 'data/hawker_geo/hawker_centre.csv'       WITH (FORMAT csv, HEADER true)
\copy hawker_proximity   FROM 'data/hawker_geo/hdb_hawker_proximity.csv' WITH (FORMAT csv, HEADER true)
\copy resale_transaction FROM 'data/hawker_geo/resale_transaction.csv'  WITH (FORMAT csv, HEADER true)
SQL

# 行数应为 12,442 / 129 / 63,340 / 986,720
for t in hdb_block hawker_centre hawker_proximity resale_transaction; do
  psql -U postgres -d sd6103_project -tAc "SELECT COUNT(*) FROM $t"
done

psql -U postgres -d sd6103_project -f scripts/hawker_geo/queries_hawker.sql
```

## 数据

输入（已在仓库）：`data/raw_data/hdb.csv`（12,442 个 block）、`data/raw_data/hawker_geo.csv`（129 家中心）、`data/raw_data/hawker_official_raw.json`（官方数据集缓存）、`data/src/resale/RESALE_CLEANED.csv`（986,720 行交易）。

产出（`data/hawker_geo/`）：`hawker_centre.csv`、`hawker_centre_match_report.csv`（join 审计）、`hdb_block.csv`、`hdb_hawker_proximity.csv`、`hdb_block_nearest.csv`、`resale_transaction.csv`、`resale_unmatched.csv`、`resale_join_report.txt`、`method_comparison.csv`、`router_validation.csv`、`onemap_validation.csv`、`onemap_distances.csv`、`onemap_full.jsonl`、`osrm_cache.jsonl`。

`hdb_hawker_walk.csv`、`hdb_hawker_walk_onemap.csv` 和 `resale_transaction.csv` 没进仓库（见根目录 `.gitignore`），都能从已提交的缓存在几秒到一分钟内重算。

## 关键数字

- 12,442 个 block × 129 家中心 → 直线 3000 m 剪枝后 101,423 个候选对 → **63,340** 对进入关系，每个 block 至少 1 个可达中心（100% 覆盖）。剪枝半径与步行上限同为 3000 m，所以「步行 3000 m 以内一条不缺」是可证明的。
- 三类分布（10,181 个住宅 block）：class 1 有 2,105、class 2 有 3,467、class 3 有 4,609，三类样本都足够，Q4 不会遇到空组。
- 交叉验证：OSRM vs OneMap（全量 63,340 对）中位差 −4.6%，7.9% 的对差 >25%；17.6% 的 block 会跨类。主表仍用 OSRM（唯一有批量接口、能全覆盖的引擎），OneMap 结果作敏感性检验，两边数字都在报告里给。
- Q4：21/26 个城镇三类齐全；结果非单调（class 2 常比 class 1 便宜），proximity 是弱信号、更像「组屋成熟度」的代理。
- Q5：1 km 内有已知菜市场的 4 房中位价 S$615,000，没有的 S$536,000（+14.7%）；把 22 家属性未知的中心算作「有」则收窄到 +5.0%。两个数都要报。
- 34.7% 的 block 在 3 km 内找不到**已知**带菜市场的中心——官方数据集 2016 年快照不收新镇的新中心，这是源数据缺口，报告里要写清楚（新镇同时距离更远、覆盖更差，两个偏差方向一致会叠加）。
