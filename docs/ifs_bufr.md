# IFS 全球热带气旋 BUFR

用户于 2026-10-01 上传的 `ECMF_BUFR_2021-2025.tar.gz`，包含 2021–2025 年
7,448 个 ECMWF 确定性热带气旋轨迹文件。上传包没有说明获取渠道或连续归档的
完整性，因此本说明仅描述实际提供的文件，不代表 ECMWF 完整历史预报库。

## 目录与保留文件

```text
data/
├── raw/baselines/ifs/
│   ├── ECMF_BUFR_2021-2025.tar.gz      # 原始压缩包，内容保持不变
│   └── 2021/ … 2025/                 # 不可变原始 BUFR
├── interim/ifs_bufr/
│   ├── file_inventory.csv            # 路径、大小、SHA-256、解码状态
│   ├── messages.csv.gz               # 初始化、风暴和有效预报元数据
│   ├── tracks_2021.csv.gz … tracks_2025.csv.gz
│   ├── cohort_tracks.csv.gz           # 关联 SID 的入选风暴完整轨迹
│   ├── matched_case_endpoints.csv.gz  # 原计划可用案例的精确点/插值端点
│   ├── qc_summary.json
│   └── storm_links.csv               # 全部消息的 IBTrACS SID 身份审计
├── catalogs/ifs_*_2026-10-01.csv       # 唯一正式事件/案例重合及年/时效汇总
└── frozen_sources/ifs_bufr/
    ├── extract_original.py            # 仅作来源参考，保留 ECMWF 版权和许可
    └── README_original.txt
```

原始归档、解包文件及完整派生表不纳入 Git。正式重合表只保存在
`data/catalogs/ifs_*_2026-10-01.csv`，不在 `interim/` 重复保存。来源校验和保存在
`data/manifests/ifs_bufr_2026-10-01.json`；结果说明见
`reports/ifs_bufr_cohort_overlap_2026-10-01.md`。

## 重建

全流程只使用已有 `ai-weather-eval` Conda 环境。BUFR 解码需要 `eccodes`；
本次运行使用 ecCodes Python 包 2.49.0。只在缺少该包时安装：

```bash
conda run --name ai-weather-eval python -m pip install eccodes
conda run --name ai-weather-eval python scripts/extract_ifs_bufr.py --extract-only
conda run --name ai-weather-eval python scripts/extract_ifs_bufr.py --match-only
```

已完成提取后，只需第二条命令重跑匹配。两步均已完成全量验证；当前环境中合并
运行曾在产物写出后的进程退出阶段出现 `free(): invalid size`，因此推荐分步执行。

`--extract-only` 只整理和解码。重复运行不会覆盖内容不同的原始文件；遇到不认识的
BUFR 结构会记录错误并终止，不默默丢弃。
`--data-root` 或 `AI_WEATHER_EVAL_DATA` 指定共享数据根目录；默认使用 `data/`。
正式重合表和清单仍写入仓库的 `data/catalogs/` 和 `data/manifests/`。
各表的 `source_file` 相对于共享数据根目录，路径为 `raw/baselines/ifs/<year>/…`。

## 字段与判定

- 时间为 UTC，`valid_time = init_time + lead_hours`。仅输出消息实际编码的时效，
  不补足到 240 h。缺失值为空，风圈半径 0 保持 0。
- `latitude/longitude` 为模型气旋中心；`pressure_hpa` 由 Pa 转 hPa，
  `max_wind_ms` 保留 m/s，`max_wind_kt` 转为 kt。
- `radius_18ms_*_km`、`radius_26ms_*_km`、`radius_33ms_*_km` 保留真实编码阈值
  18/26/33 m/s；`*` 为 ne/se/sw/nw。不得把这些近似阈值直接改称精确 34/50/64 kt。
- 本包的 `member_number=52` 表示 HRES 确定性预报，不是观测成员或 52 个集合成员。
  BUFR 第一组中心位置是观测元数据，第二组是模型初始位置；两者分别保存。
  模型初始位置缺失时，不用观测位置替代。
- `positive_position_count` 只数 `lead_hours>0` 且经纬度有效的模型点。
  只有观测或 T+0 的文件不算有效预报。
- 风暴身份用起报同时间的观测位置在 IBTrACS main track 中核验：WGS84 距离
  ≤150 km；若多个候选都在阈值内，第二候选必须比第一候选远至少 75 km。
  ATCF 区域及序号一致性另作诊断；名称不决定匹配，南半球季节年由 SID 源记录确定。
- 固定预报案例只接受同 SID、同原计划 `init_time`；不会改用邻近起报周期。
  参考时间覆盖要求有该时刻有效模型点，或有间隔 ≤6 h 的两个有效端点。
  `interpolated` 仅表示具备插值端点，本阶段没有计算插值轨迹或误差。
- `storm_has_forecast` 是同风暴任意时次有有效预报；`event_time_covered` 进一步要求
  事件发生前起报且参考时间被覆盖；`forecast_available` 进一步要求采用原计划起报。
  重复同 SID、同起报的完全一致预报只计一次；冲突预报整组拒绝。
- 风和气压可用性分别检查同一时刻或插值端点，不把单独的轨迹可用等同于强度可用。
  BUFR 风速不在本次直接与 IBTrACS 的一分钟 `USA_WIND` 比较。

解码结构及成员含义参考 ECMWF 官方的
[BUFR 示例](https://confluence.ecmwf.int/spaces/ECC/pages/52462870/bufr_read_tropical_cyclone)
和[风圈产品说明](https://confluence.ecmwf.int/spaces/FCST/pages/177485373/New%2BTropical%2BCyclone%2BWind%2BRadii%2Bproduct)。
