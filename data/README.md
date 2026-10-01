# Data contract

统一按处理阶段存放数据，数据集内部不再另设一套 `raw/` 或 `extracted/`。
默认本地数据根目录为 `data/`；使用外部数据盘时设置 `AI_WEATHER_EVAL_DATA`。
大型原始数据和可重建中间表不纳入 Git。

```text
data/                                  # 或 $AI_WEATHER_EVAL_DATA
├── raw/
│   ├── ibtracs/
│   ├── era5/
│   ├── coastline/
│   ├── forecasts/<model>/
│   └── baselines/ifs/                  # 原始压缩包及 2021/ … 2025/ BUFR
├── interim/
│   ├── case_catalog/
│   ├── canonical_forecasts/
│   ├── regridded_fields/
│   ├── detected_tracks/
│   └── ifs_bufr/                       # 解码轨迹、身份审计、匹配端点和 QC
├── processed/
│   └── verification/
├── cache/
├── catalogs/                          # 唯一正式事件、案例和 IFS 重合表
├── handoffs/                          # Weather Hub 推理输入及关联索引
├── frozen_sources/                    # 固定 IBTrACS 提取及 IFS 原始参考脚本
├── manifests/                         # 来源、版本、文件路径和校验和
└── samples/                           # 小型测试输入
```

约束：

- `raw/` 视为只读，不在原始文件上做覆盖修改。
- `interim/` 可以重建，保存规范化和计算代价较高的中间产物。
- `processed/` 保存可直接进入统计与绘图的整洁表。
- 每个原始数据集都要在 `data/manifests/` 登记版本、来源和校验和。
- `data/catalogs/` 保存经质检、带来源清单的小型正式研究样本，并纳入 Git。
- `data/frozen_sources/` 保存经校验、压缩的固定研究源文件，供重建正式样本。
- `data/samples/` 只保存经过裁剪且不敏感的小型测试数据。

外部数据盘只需保存 `raw/`、`interim/`、`processed/` 和 `cache/`；正式目录、
交接文件、冻结源、清单和测试样本仍保存在仓库的 `data/` 中。
同一结果只保留一份正式版本，脚本直接引用该文件，不在中间目录另存副本。
来源文件及重建所需输入不视为旧输出。

IFS 提取及文件说明见 [../docs/ifs_bufr.md](../docs/ifs_bufr.md)。
