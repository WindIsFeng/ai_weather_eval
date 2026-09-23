# Data dictionary

## Coastal-impact case table

| Field | Meaning |
| --- | --- |
| `case_id` | `SID-CE##` 格式的稳定事件键 |
| `storm_id` | IBTrACS `SID`，唯一风暴身份 |
| `episode_index` | 同一 `SID` 按时间排序的沿海接触序号 |
| `episode_start`, `episode_end` | 沿海接触时段（UTC） |
| `closest_approach_time` | 事件内中心距岸最近时刻（UTC） |
| `reference_event` | `landfall` 或 `coastal_approach` |
| `reference_time` | 登陆事件的首次海→陆交点时刻；未登陆事件的最近岸时刻（UTC） |
| `basin` | IBTrACS 海盆代码；`NA` 表示北大西洋 |
| `latitude`, `longitude` | 参考事件的中心位置或登陆交点，东经为正 |
| `nearest_coast_latitude`, `nearest_coast_longitude` | 最近 GSHHG 海岸点 |
| `minimum_coast_distance_km` | WGS84 椭球测地线最短距离；穿越时为 0 |
| `coastal_episode_vmax_kt` | 接触事件前后各 6 h 窗口中的 `USA_WIND` 最大值 |
| `closest_approach_vmax_kt`, `reference_vmax_kt` | 最近岸时和参考时刻的 `USA_WIND` |
| `center_surface_at_closest_approach` | 最近岸时中心为 `land` 或 `sea` |
| `center_surface_at_reference` | 参考时中心为 `coastline` 或 `sea` |
| `landfall_crossing`, `landfall_count` | 是否及多少次发生海→陆穿越 |
| `coastline_exit`, `exit_count` | 事件窗口内是否及多少次发生陆→海穿越 |
| `tangent_crossing_count` | 未改变 land/sea 状态的海岸线切触次数 |
| `coastline_crossing`, `crossing_count` | `landfall_crossing` 的兼容字段 |
| `landfall_vmax_kt` | 海→陆交点处插值风速的事件最大值 |
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
`reference_dataset`、`variable`、`domain` 和 `qc_flag`。路径与事件坐标以
IBTrACS/GSHHG 为参考，网格强度和动力场以 ERA5 为参考；同名指标不能混用不同
参考资料而不注明来源。

## Forecast case table

`weather-eval catalog plan` 从事件目录生成一行一个 `case_id` × 名义提前量的
起报计划。`forecast_case_id` 为 `<case_id>-L<三位小时数>`；
`reference_event`、`reference_time` 和 `reference_latitude`/`reference_longitude`
固定观测事件。`target_init_time` 是参考时刻减去名义提前量；`init_time` 是匹配的
标准起报周期；`actual_lead_hours` 和 `cycle_offset_hours` 记录实际偏移。
`schedule_status` 标记 `matched` 或 `no_cycle_within_tolerance`。本表不代表
模型输出已经存在，模型覆盖情况在后续导入时另行统计。

## Canonical forecast fields

标准维度为 `init_time`、`lead_time`、`latitude`、`longitude`。变量名为 `msl`、`u10`、
`v10`、`z500`、`u850`、`v850`、`u200`、`v200`。单位规范和气压层坐标将在适配器实现
阶段冻结。
