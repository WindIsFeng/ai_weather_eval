# Data dictionary

## Landfall case table

| Field | Meaning |
| --- | --- |
| `case_id` | `storm_id` 与登陆序号组成的稳定主键 |
| `storm_id` | IBTrACS SID |
| `landfall_index` | 同一风暴按时间排序的登陆序号 |
| `basin` | 登陆事件所属海盆 |
| `landfall_time` | 观测海岸线穿越时刻（UTC） |
| `latitude`, `longitude` | 观测登陆坐标，东经为正 |
| `lifetime_vmax_kt` | 生命周期一分钟最大持续风速 |
| `landfall_vmax_kt` | 插值到登陆时刻的最大风速 |
| `landfall_mslp_hpa` | 插值到登陆时刻的最低海平面气压 |
| `qc_status` | `pending`、`accepted`、`corrected` 或 `excluded` |

## Metric table

每行只保存一个模型、案例、起报、有效时间和指标值。稳定字段为 `case_id`、`storm_id`、
`model`、`init_time`、`nominal_lead_hours`、`valid_time`、`metric`、`value`、`unit`、
`variable`、`domain` 和 `qc_flag`。

## Canonical forecast fields

标准维度为 `init_time`、`lead_time`、`latitude`、`longitude`。变量名为 `msl`、`u10`、
`v10`、`z500`、`u850`、`v850`、`u200`、`v200`。单位规范和气压层坐标将在适配器实现
阶段冻结。

