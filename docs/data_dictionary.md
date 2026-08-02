# Data dictionary

## Coastal-impact case table

| Field | Meaning |
| --- | --- |
| `case_id` | `SID-CE##` 格式的稳定事件键 |
| `storm_id` | IBTrACS `SID`，唯一风暴身份 |
| `episode_index` | 同一 `SID` 按时间排序的沿海接触序号 |
| `episode_start`, `episode_end` | 沿海接触时段（UTC） |
| `closest_approach_time` | 事件内最近海岸时刻（UTC） |
| `basin` | IBTrACS 海盆代码；`NA` 表示北大西洋 |
| `latitude`, `longitude` | 最近海岸的路径点或穿越点，东经为正 |
| `nearest_coast_latitude`, `nearest_coast_longitude` | 最近 GSHHG 海岸点 |
| `minimum_coast_distance_km` | WGS84 椭球测地线最短距离；穿越时为 0 |
| `coastal_episode_vmax_kt` | 接触事件前后各 6 h 窗口中的 `USA_WIND` 最大值 |
| `coastline_crossing` | 路径是否与海岸线相交 |
| `r34_contact`, `r50_contact`, `r64_contact` | 相应风圈是否达到海岸 |
| `tier` | 沿海影响层级 A、B、C 或 D |
| `primary_sample` | 是否属于主样本（A+B） |
| `provisional_track` | 是否含非 `main` 轨迹 |
| `qc_status` | `pending`、`accepted`、`corrected` 或 `excluded` |

CSV 中的北大西洋代码为字面值 `NA`。使用 pandas 读取时应设置
`keep_default_na=False`，避免将该海盆代码误认为缺测值。

## Storm table

`selected_storms.csv` 每个 `SID` 一行，包含事件数、最高层级、接触时间范围、
最小距岸距离、强度、是否穿越海岸线以及轨迹版本状态。

## Metric table

每行只保存一个模型、案例、起报、有效时间和指标值。稳定字段为 `case_id`、`storm_id`、
`model`、`init_time`、`nominal_lead_hours`、`valid_time`、`metric`、`value`、`unit`、
`variable`、`domain` 和 `qc_flag`。

## Canonical forecast fields

标准维度为 `init_time`、`lead_time`、`latitude`、`longitude`。变量名为 `msl`、`u10`、
`v10`、`z500`、`u850`、`v850`、`u200`、`v200`。单位规范和气压层坐标将在适配器实现
阶段冻结。
