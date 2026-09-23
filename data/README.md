# Data contract

本目录的 `catalogs/` 保存小型、经质检的版本化事件与预报案例表；
`frozen_sources/` 保存可重建正式样本的压缩 IBTrACS 提取文件。大型原始气象数据
仍不纳入 Git。设置 `AI_WEATHER_EVAL_DATA` 后，数据根目录采用：

```text
$AI_WEATHER_EVAL_DATA/
├── raw/
│   ├── ibtracs/
│   ├── era5/
│   ├── coastline/
│   ├── forecasts/<model>/
│   └── baselines/<model>/
├── interim/
│   ├── case_catalog/
│   ├── canonical_forecasts/
│   ├── regridded_fields/
│   └── detected_tracks/
├── processed/
│   └── verification/
└── cache/
```

约束：

- `raw/` 视为只读，不在原始文件上做覆盖修改。
- `interim/` 可以重建，保存规范化和计算代价较高的中间产物。
- `processed/` 保存可直接进入统计与绘图的整洁表。
- 每个原始数据集都要在 `data/manifests/` 登记版本、来源和校验和。
- `data/catalogs/` 保存经质检、带来源清单的小型正式研究样本，并纳入 Git。
- `data/frozen_sources/` 保存经校验、压缩的固定研究源文件，供重建正式样本。
- `data/samples/` 只保存经过裁剪且不敏感的小型测试数据。
