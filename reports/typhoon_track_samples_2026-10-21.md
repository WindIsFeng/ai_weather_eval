# 三场随机风暴的 best track 与模型路径对照

从冻结全球样本的 83 个 `storm_id` 中等概率、无放回抽取 3 场，随机种子为
`20261021`；未按预测误差、追踪成功率或资料完整率筛选。每场展示参考时间最早的
沿海事件，及其 24、48、72、96、120 小时五种名义提前量。

| 风暴 | SID | 事件 | 有五模型路径的名义提前量 | `no_seed` 的名义提前量 |
| --- | --- | --- | --- | --- |
| Ian（2022） | `2022266N12294` | CE01 | 24、48、72、96 h | 120 h |
| Ernesto（2024） | `2024225N14313` | CE01 | 24 h | 48、72、96、120 h |
| Carlotta（2024） | `2024213N14254` | CE01 | 24、48 h | 72、96、120 h |

三个 SID 来自当前全球气旋样本，本次抽样没有限定西北太平洋海盆。

## 看图方式

- 黑线为冻结 IBTrACS `main` 的 `LAT/LON`，彩线为五模型实际提取的预测中心。
- 每个面板的 best track 与模型使用相同起报至预报结束时间窗口；模型预测从 +6 h 开始。
- 空心星号为观测起报位置；模型标记位于每 24 h 及最后有效点。追踪中途停止用 `×` 标记。
- `L024` 等为名义事件提前量；面板同时写明实际起报时间与预报长度，时间均为 UTC。
- `no_seed` 面板保留 best track，并注明五模型预测路径缺失；不使用之后的观测位置补播种。
- 同一风暴的五个面板使用相同地图范围；不同风暴分别取适合自身路径的范围。
- 观测资料间隔超过 6 h 时断开连线，缺测预测也不补点或连线。

三张图均以 600 DPI TIFF 输出，另存 120 DPI PNG 预览。Arial 在本机不可用，
按项目规则使用 Liberation Sans 16 pt，未添加图标题。字体由
[Debian Liberation 字体包](https://deb.debian.org/debian/pool/main/f/fonts-liberation2/fonts-liberation2_2.1.5-1_all.deb)
解包到项目的 `data/cache/fonts/liberation2/`，未修改系统字体。

## 文件与复现

结果目录：`outputs/typhoon_track_samples_2026-10-21/`。

- `2022_ian_2022266N12294.tif` 和 `_preview.png`。
- `2024_ernesto_2024225N14313.tif` 和 `_preview.png`。
- `2024_carlotta_2024213N14254.tif` 和 `_preview.png`。
- `sampled_cases.csv`、`sampled_tracks.csv`、`plotted_best_track.csv`：保留实际用于绘图的记录。
- `manifest.json`：抽样总体与种子、选择规则、源文件与脚本哈希、字体、面板状态和地图范围。
- `validation.json`：绘图记录与输入结果的对应检查、观测时间窗口与 TIFF 规格检查。

```bash
conda run --name ai-weather-eval python scripts/plot_sample_tracks.py
```

可使用 `--seed` 重新随机抽样，或通过 `--storm-ids` 指定其他 SID；建议同时指定新的
`--output-dir` 保留本次图片。跨主机复现时，安装所需的 `matplotlib`、`cartopy`，
并提供 Arial/Liberation Sans；`--font-path` 接受其 regular TTF 文件路径。
