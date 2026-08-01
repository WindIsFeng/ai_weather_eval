# Runtime outputs

`outputs/<experiment>/<run_id>/` 由工作流生成，建议包含：

```text
resolved_config.yaml
manifest.json
logs/
qc/
metrics/
tables/
figures/
```

除本说明外，本目录被 Git 忽略。`run_id` 应由 UTC 时间和配置摘要组成，避免覆盖历史结果。

