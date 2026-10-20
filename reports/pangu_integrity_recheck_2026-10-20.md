# Pangu 重新下载后的完整性复查（2026-10-20）

复查时间：2026-10-20 14:11:08—14:21:41（Asia/Shanghai）。
数据盘按原 UUID 只读挂载到 `/mnt/usb`。本次仅复查 Pangu，保留首次五模型审计记录。

**结论：全部通过。** Pangu 的全部 570 个案例、1,140 个 NetCDF 文件已完成清单、大小、结构和逐时次抽样检查；此前截断的两个高空文件均额外完成全量数值读取。

| 检查 | 结果 |
| --- | --- |
| 案例覆盖 | 570/570 |
| 文件清单和 manifest | 0 个错误 |
| NetCDF 结构 | 1140 个，失败 0 个 |
| 逐时次抽样 | 1140 个，失败 0 个 |
| NetCDF 总字节数 | 1,298,702,007,770，与原始记录一致 |
| JSON / CSV 解析 | 1,150 / 573 个，错误 0 个 |
| 元数据与首次记录的 SHA-256 比较 | 0 个变化 |

## 两个修复文件

| 案例 | 原始预期字节数 | 重新下载后字节数 | 全量读取数值个数 | 结果 |
| --- | ---: | ---: | ---: | --- |
| `2023271N14144-CE03-L072` | 2,060,534,744 | 2,060,534,744 | 809,827,200 | 通过 |
| `2023279N08157-CE01-L096` | 2,914,672,873 | 2,914,672,873 | 1,147,255,200 | 通过 |

两个文件均为各案例目录内的 `upper.nc`。已读取全部 `z/q/t/u/v` 变量，覆盖全部预报时次、13 个气压层以及 721×1440 完整网格；共检查 1,957,082,400 个数值。完整读取过程中没有解码错误、掩码缺测、NaN 或 Inf。

本次计算并保存两个文件的 SHA-256，供后续复制或存储检查使用：

- `2023271N14144-CE03-L072/upper.nc`：`b0af7ee1ece9343239c9ea0e3d840f99a383295c3913695ef44e7e86d2b64f8a`。
- `2023279N08157-CE01-L096/upper.nc`：`9b32991a74dca6f4984ae04c2661326cff115f0905caa37568ad639099b16535`。

## 范围与复核

所有 Pangu 文件重复执行独立冻结案例基线的身份、起报时间、预报长度、6 小时间隔、完成状态、manifest 大小和计时表检查，以及 NetCDF 维度、坐标、气压层、时间轴、变量、数据类型和单位检查。

所有可读文件的每个变量、每个预报时次抽取一个轮换的 2×2 网格片段；高空场逐时次覆盖 200/500/850 hPa 及一个轮换层，首末时次覆盖全部 13 层。本次共抽样 862,220 个数值，覆盖 14,270 个“文件×预报时次”。

逐文件明细与汇总已逐项核对一致；既有 5 项故障检测测试和复核 notebook 代码单元均通过。检查前后设备 I/O 错误计数均为 `0x5`，没有新增设备错误，挂载保持只读。

全量数值扫描仅针对上述两个修复文件，其余文件通过结构和逐时次抽样检查。尚无源端原始输出文件的 SHA-256；本次新计算的哈希提供后续比对基线，不能单独证明与源端原始输出逐字节相同，也不判断预报物理准确性。

- [修复文件复查 CSV](tables/pangu_repaired_files_recheck_2026-10-20.csv)
- [首次五模型审计](inference_data_integrity_2026-10-20.md)
- [复核 notebook（含本次 Pangu 后续结果）](../notebooks/audit_inference_integrity.ipynb)
- [审计脚本](../scripts/audit_inference_data.py)
- 本机原始证据目录：`outputs/pangu_integrity_recheck_2026-10-20/`，包括 `summary.json`、`structure.jsonl`、`sample.jsonl`、`pangu-inventory.csv`、`metadata_readability.json`、`repaired_full_read.json`、`full_read_repaired.py`。

重跑 Pangu 常规复核（使用新目录保留本次证据）：

```bash
conda run --name ai-weather-eval --no-capture-output python scripts/audit_inference_data.py \
  --models pangu --output outputs/pangu_integrity_next --workers 4
```

执行时审计脚本 SHA-256：`927e0ad3365dc608b2ce21fd5f3fa8f82bff9a6a658a48d93876ac8909a4904d`。
