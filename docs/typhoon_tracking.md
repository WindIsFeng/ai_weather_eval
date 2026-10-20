# 五模型台风路径 tracker

`weather-eval track` 从 GraphCast、FengWu、FuXi、Pangu 和 Aurora 的 Weather Hub
NetCDF 结果中提取预报路径，供后续按 `storm_id` 和 `valid_time` 与 best track 配对。
默认使用冻结的 570 案例计划，每模型保留 570 条案例记录及 7,135 个计划时次，
五模型合计 2,850 条案例和 35,675 条时次。输入预测文件只读，结果写入项目输出目录。

## 运行与选择

在仓库目录使用现有 Conda 环境：

```bash
conda run --name ai-weather-eval weather-eval track \
  --root /mnt/usb \
  --workers 4 \
  --output /scratch/hufeng/ai_weather_eval/outputs/typhoon_tracks
```

程序通过 `job.json` 自动识别模型目录，读取每案例 `surface.nc` 与 `upper.nc`；
Aurora 使用 `atmospheric.nc` 和地面风变量 `10u/10v`。`pangu_weather` 是
`pangu` 的输入别名，结果统一保存 `model=pangu`。默认处理全部五模型与全部案例。

| 参数 | 默认值及用途 |
| --- | --- |
| `--root` | `/mnt/usb`；包含模型任务子目录的根目录，也可为单个任务目录 |
| `--output` | 必填；新结果目录，或配合 `--resume` 的已有结果目录 |
| `--cases-file` | 仓库内 `data/handoffs/weather_hub_cases_2022_2024_v2026-09-22.csv` |
| `--ibtracs` | 仓库内 `data/frozen_sources/ibtracs.since1980.2026-09-22.study_range.csv.gz` |
| `--models` | 一个或多个模型名，以空格分隔；省略时选择全部五模型 |
| `--case-ids` | 一个或多个完整 Weather Hub 案例 ID，以空格分隔 |
| `--workers` | 默认 4；设为 1 使用串行路径，须为正整数 |
| `--resume` | 复用输入、选择、参数与代码均一致的案例检查点 |
| `--search-radius-km` | 445；相对上一中心的候选搜索半径，km |
| `--diagnostic-radius-km` | 278；候选周围涡度与风速诊断半径，km |
| `--vorticity-threshold-s` | `5e-5`；气旋性相对涡度阈值，s⁻¹，使用严格大于判断 |

例如先跑一个模型、一个案例：

```bash
conda run --name ai-weather-eval weather-eval track \
  --root /mnt/usb --models pangu \
  --case-ids 2022024S09090-CE01-L024 \
  --workers 1 \
  --output /scratch/hufeng/ai_weather_eval/outputs/typhoon_track_example
```

`forecast_case_id` 的 `L024/L048/...` 表示相对沿海事件的名义提前量，
不是该文件的所有预报时效。`lead_hours` 才是每行实际时效；例如冻结计划中
`2022024S09090-CE01-L024` 的 `forecast_hours=30`，输出 6、12、18、24、30 h。
不输出 0 h 观测点。`valid_time = init_time + lead_hours`，所有时间采用 UTC。

已有案例目录、模型名、起报时间、预报长度和起报观测坐标时，也可直接调用 Python 接口：

```python
from ai_weather_eval.tracking import track_forecast
from ai_weather_eval.tracking.reader import WeatherHubForecast

with WeatherHubForecast(case_dir, model, init_time, forecast_hours) as forecast:
    points = track_forecast(
        forecast, seed_latitude=init_lat, seed_longitude=init_lon,
    )
```

`points` 是每个计划时次的字典列表，包含位置、诊断值和状态；该函数不写结果文件。
`init_lat/init_lon` 应来自起报时刻，模型名使用 `pangu` 等原生名称。

## 追踪方法与边界

本方法参考 Bi et al. (2023),
[Accurate medium-range global weather forecasting with 3D neural networks](https://doi.org/10.1038/s41586-023-06185-3)
中 Pangu-Weather 的局部海平面气压最低点与涡度筛选思路，属于精简实现，
不是论文算法的完整复现。

1. 从冻结 IBTrACS `main` 路径以 `SID + init_time` 精确查找起报位置。
   不插值起报位置；冻结计划每模型 470 案例有种子，100 案例为 `no_seed`。
2. 每隔 6 h，在上一中心 445 km 内寻找有完整 3×3 邻域的 MSLP 局地最低点。
   第一个预报时次以上述起报位置作为搜索中心。平坦气压场不构成局地低压中心。
3. 候选中心 278 km 内最大气旋性 850 hPa 相对涡度须超过 5×10⁻⁵ s⁻¹。
   北半球取相对涡度，南半球取其负值。多个候选合格时选择中心气压最低者，
   同压时选择离上一中心最近者；剩余并列以坐标顺序确定，保证可复现。
4. 记录中心网格点气压及 278 km 内最大 10 m 风速。没有暖心、陆海分类和
   风速筛选条件，也不做路径平滑或网格点之间的中心拟合。
5. 没有合格中心即终止该路径，保留之前的成功点。后续时次留空并标为
   `terminated`；不使用未来 best track 或在丢失后用观测重置中心。

距离使用 WGS84 椭球测地线。850 hPa 涡度为球面有限差分，包含纬度度量项，
正确处理两种纬度顺序和经度接缝。数据使用原生网格，仅读取当前时次、所需气压层
及搜索与诊断所需局部窗口，并增加两格边界；不加载全球场或完整时间序列。
每个必需变量的 NetCDF 块缓存限制为 2 MiB。文件完成状态、时效、起报/有效时间、
上下层网格和变量单位在使用前校验；掩码缺测转为 NaN，缺失诊断导致该候选被拒绝。

`vmax10_ms` 是模型原生网格上的邻域诊断量。它尚未与 ERA5 统一网格及提取口径，
也不等同于 IBTrACS `USA_WIND` 的 1 分钟最大持续风，不能直接作为同口径强度验证。
本命令完成路径提取与质量统计；best track 路径误差计算另行进行。

## 结果与缺测

| 产物 | 内容 |
| --- | --- |
| `tracks.csv` | 每个模型、案例、计划时次一行；中心与气压、风速、涡度诊断，以及状态与原因 |
| `cases.csv` | 每个模型 × 预报案例一行；种子、追踪终点、点数、状态、耗时和检查点信息 |
| `summary.json` | 总计及分模型的覆盖、状态、失败原因、耗时、内存与运行来源 |
| `run.json` | 运行指纹、参数、选择、冻结资料与实现代码哈希 |
| `checkpoints/` | 主进程按案例写入的 JSON 完整进度，供续跑复用 |

`tracks.csv` 同时保存完整 `forecast_case_id`、事件 `case_id`、风暴 `storm_id`、
模型、起报和有效时间。经度统一为 `[-180, 180)`；纬度单位为度，`msl_hpa` 为 hPa，
`vmax10_ms` 为 m s⁻¹，`cyclonic_vorticity_s` 为 s⁻¹。字段详情见
[数据字典](data_dictionary.md#extracted-forecast-tracks)。

| 时次状态 | 含义 |
| --- | --- |
| `ok` | 找到合格的预测中心，诊断值有效 |
| `no_seed` | 起报时刻没有精确匹配的 IBTrACS `main` 位置；该案例全部时次保留空值 |
| `track_lost` | 当前时次没有合格中心，包括低压或涡度筛选失败、诊断资料缺失 |
| `input_error` | 当前时次或文件读取、格式校验失败；具体异常在 `reason` |
| `terminated` | 先前时次已经追踪失败或输入出错，当前时次未继续搜索 |

案例状态 `complete` 表示所有时次为 `ok`；其他案例状态取首次失败的状态。
空白数值表示缺测，不是零。所有计划时次都保留，因此覆盖率必须同时报告
`no_seed`、追踪失败和输入错误，不应仅按成功路径统计模型能力。

## 续跑与并行性能

中断后使用原命令增加 `--resume`。检查点通过临时文件写入后原子替换；主进程统一
保存进度和汇总，最终 CSV 按稳定模型、案例与时效顺序输出。
只有指纹一致的成功或无种子检查点可复用：运行指纹覆盖模型/案例选择、阈值、案例表与 IBTrACS
内容哈希、实现代码；案例指纹还覆盖种子及输入文件路径、大小、修改时间等身份信息。
输入 NetCDF 使用文件身份元数据，续跑不扫描大型预测文件的全量哈希。
包含 `input_error` 的案例在运行指纹一致时重试，也允许其预测文件修复后的大小或
修改时间发生改变。成功和 `no_seed` 案例的输入身份改变时拒绝复用。
案例表、IBTrACS、代码、阈值或选择改变后使用新的输出目录，避免混合不同运行的结果。

多核任务粒度为一个模型 × 一个预报案例；单条路径内部仍按时间顺序计算。
`ProcessPoolExecutor` 使用 `spawn`，每个进程独立打开、关闭 NetCDF 文件，
不共享 HDF5 句柄。默认 4 个进程，每进程数值库线程数限制为 1，避免嵌套线程竞争。
进程数取决于存储吞吐与计算开销，不直接设为主机全部逻辑 CPU。

使用固定的 50 个有种子任务比较进程数：

```bash
conda run --name ai-weather-eval python scripts/benchmark_tracker.py \
  --root /mnt/usb \
  --output /scratch/hufeng/ai_weather_eval/outputs/typhoon_tracker_benchmark
```

基准覆盖五模型、两半球和不同预报长度，在预热缓存后交错运行 1、2、4、8、16 进程，
各重复两次，报告耗时、吞吐和内存，并校验路径输出与串行结果完全一致。
若 16 进程相对 8 进程至少提速 10%，继续测 32 进程；选择实测最快者，
耗时差在 5% 内时取进程更少者。缓存预热结果反映重复处理的性能，
全量运行耗时另行报告。实际性能选择与最终覆盖统计以运行报告为准。

## 个例路径图

用固定随机种子从风暴 SID 抽取三场，并将首个沿海事件的五种起报提前量与 best track 叠加：

```bash
conda run --name ai-weather-eval python scripts/plot_sample_tracks.py
```

输出 600 DPI TIFF 和 PNG 预览；缺少起报位置或追踪失败的面板保留明确标记。
抽样规则、首批结果和文件说明见
[三场风暴路径对照](../reports/typhoon_track_samples_2026-10-21.md)。
