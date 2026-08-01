# AI Weather Evaluation

评估 Pangu-Weather、FengWu、FuXi、GraphCast、Aurora 等 AI 天气模型对
2022–2024 年全球登陆强热带气旋的预测表现。

本仓库只负责案例管理、模型输出适配、评估、统计和绘图；模型推理及大体量数据
存储位于仓库外。

## 研究范围

- 样本：登陆发生于 2022-01-01 至 2024-12-31，且生命周期一分钟最大持续风速
  达到 64 kt 的全球热带气旋。
- 事件：同一风暴的不同登陆分别记录，统计时按风暴聚类。
- 起报：登陆前 24、48、72、96、120 小时附近的标准 6 小时起报周期。
- 指标：路径、最大风速、最低海平面气压、登陆时间/位置/强度及核心动力场。
- 环境场：海平面气压、10 米风、500 hPa 位势高度、850/200 hPa 风；首版不含降水。

详细口径见 [docs/methodology.md](docs/methodology.md)。

## 快速开始

本项目统一使用已有的 `ai-weather-eval` Conda 环境，Python 版本固定为 3.12。
当前不预装全部项目依赖；开发过程中缺少哪个包，就在该环境中通过 pip 按需安装。
不要使用系统 Python、Conda `base`、uv 或另一个项目虚拟环境运行本项目。

```bash
conda activate ai-weather-eval
python --version
```

非交互式脚本和自动化任务统一显式指定环境：

```bash
conda run --name ai-weather-eval python <script.py>
conda run --name ai-weather-eval python -m pytest
```

后续确实需要依赖时再安装，不做预安装：

```bash
conda run --name ai-weather-eval python -m pip install <package>
```

需要使用项目 CLI 时，再以 editable 模式安装本地包：

```bash
conda run --name ai-weather-eval python -m pip install --editable .
export AI_WEATHER_EVAL_DATA=/path/to/ai_weather_eval_data
weather-eval config check --config configs/experiments/global_landfall_2022_2024.yaml
weather-eval --help
```

`environment.yml`只记录环境名、Python 3.12 和 pip，不维护具体科研包；具体 Python
依赖仍记录在 `pyproject.toml`，但是否安装由当前开发任务决定。

当前提交是项目骨架。工作流命令和接口已经建立，但具体 IBTrACS 筛选、气旋追踪、
指标计算和绘图算法将在后续实现。

## 目录职责

- `configs/`：数据集、模型、实验和绘图配置。
- `data/`：只保存数据说明、清单和小型测试样例。
- `src/ai_weather_eval/`：所有可测试的生产代码。
- `tests/`：单元测试、集成测试和小型夹具。
- `notebooks/`：探索、人工质控和成果审阅，不承载核心流程。
- `outputs/`：运行产物，不纳入 Git。
- `reports/`：人工确认后的论文图表，可纳入 Git。

## 外部数据

真实数据根目录由 `AI_WEATHER_EVAL_DATA` 指定，推荐布局见
[data/README.md](data/README.md)。禁止将 NetCDF、GRIB 或 Zarr 大文件提交到仓库。
