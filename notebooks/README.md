# Notebooks

`plot_ab_tracks.ipynb` 使用冻结的 A+B 事件表绘制风暴路径，最终图保存为
`reports/figures/ab_typhoon_tracks.tif`（600 DPI，Arial 16 pt；不可用时用
Liberation Sans）。

使用已有 `ai-weather-eval` 环境。提交时清除执行计数和内嵌输出，避免保存旧图、
旧统计及机器路径。核心算法放在 `src/ai_weather_eval/`，notebook 用于探索和图件审阅。

