# Data contract

本目录不保存真实气象数据。设置 `AI_WEATHER_EVAL_DATA` 后，外部数据根目录采用：

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
- 仓库只允许在 `data/samples/` 中保存经过裁剪且不敏感的小型测试数据。

