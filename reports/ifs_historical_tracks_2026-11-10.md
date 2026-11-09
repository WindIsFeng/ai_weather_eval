# IFS 历史轨迹与强度补充

在同一批 570 个冻结案例中，事件参考时刻同时具备位置、最大风速和中心气压的
案例从 **284（49.8%）增加到 312（54.7%）**。新增 28 个，分属 AWS 确定性
BUFR 18 个和 NCAR 确定性 CXML 10 个。

| 来源 | 取得时次 | 本地原始文件 | 新增可用 case |
| --- | ---: | --- | ---: |
| AWS IFS | 50/89 | 50 份完整 BUFR | 18 |
| NCAR TIGGE ECMWF | 83/83 | 18 份完整 CXML，65 份已验证的确定性字节范围 | 10 |

本轮 AWS 重试取回 11 份 BUFR。两个此前缺失的 72 h 案例通过核验，另有
4 个原由 CXML 覆盖的案例按同格式优先规则改用 AWS。剩余 39 个 AWS 对象仍返回
HTTP 503。NCAR 对原 BUFR 缺失的案例独立核验得到 17 个，其中 7 个选用了 AWS。
所有已取得的对象均通过确定性轨迹解析。

| 名义提前量 | 原有 case | AWS 补入 | NCAR 补入 | 合计 |
| --- | ---: | ---: | ---: | ---: |
| 24 h | 97 | 5 | 3 | 105/114 |
| 48 h | 87 | 5 | 2 | 94/114 |
| 72 h | 65 | 7 | 2 | 74/114 |
| 96 h | 19 | 1 | 2 | 22/114 |
| 120 h | 16 | 0 | 1 | 17/114 |

至少一个原计划 case 可用的事件由 99 增至 109 个。允许任意事件前起报的事件覆盖
由 110 增至 112 个。ROSLYN 的 24 h、48 h 由 NCAR 补入，BEATRIZ 的 48 h 由
AWS 补入。ROSLYN 72 h 的 CXML 给出两个同起报版本，部分时效风速相差
0.1 m/s，按冲突规则排除。MEGAN 和 DEBBY 的这两个事件仍缺合格轨迹。

用同 SID、起报和正时效配对后，AWS 与原 BUFR 的 680 个共同点位置、气压和风速
一致。NCAR 与原 BUFR 的 594 个共同点位置和气压一致，565 个点的风速一致，
其余点的最大风速差为 0.1 m/s。独立核验覆盖了 133 个原始下载、570 个冻结案例、
617 个实际支持点和全部发布产物的校验和。项目测试 158 项通过。

## 来源和匹配

请求清单只包含原 BUFR 审计缺失案例的原计划起报时次。AWS 候选为确定性
`oper/scda` 的 `tf.bufr` 对象，并要求文件名标注的时长能够到达至少一个缺失
案例的参考时刻。NCAR 候选为 ECMWF 的 `ifs_glob_prod_all_glo` CXML 对象。
同一来源、同一起报的文件只请求一次。文件是否存在与是否含有对应风暴分别审计。
[AWS 公开数据集](https://registry.opendata.aws/ecmwf-forecasts/) ·
[NCAR TIGGE 档案](https://gdex.ucar.edu/datasets/d330003/)。

- AWS 解码实际编码的 HRES 52 号成员。NCAR 使用 ECMWF 的
  `data type="forecast"` 确定性块，成员编号保留为空，并用同一文件中
  `analysis` 块的同期 synoptic fix 作为身份锚点。保留完整 XML，或保留
  从第 0 字节起、经解析确认含有完整分析和确定性块的原始 HTTP 字节范围。
  集合记录和 70–99 号的 ECMWF 生成扰动不参与此队列匹配。
  [CXML 规范](https://gdex.ucar.edu/datasets/d330003/documentation/) ·
  [ECMWF 气旋编号](https://confluence.ecmwf.int/spaces/FCST/pages/51727210/Tropical%2BCyclone%2Btracks%2Bin%2BBUFR%2B-%2Bincluding%2Bgenesis)。
- 保持冻结的 570 个案例和原计划 UTC 起报。同一时刻的 IBTrACS 主轨迹位置
  与来源的观测位置须在 150 km 内。存在多个候选时，最近两者距离须相差
  至少 75 km。精确的参考时刻或相隔不超过 6 h 的两侧有效点均可计入，
  并同时要求端点有最大 10 m 风速和中心气压。
- 对同源、同 SID、同起报的重复轨迹，完全相同的只计一次，冲突组排除。
  每个案例按上传 BUFR、AWS BUFR、NCAR CXML 的顺序选择一条完整轨迹。
  CXML 的风圈字段留空。CSV 保留原始时效和实际支持点，覆盖统计尚未计算插值误差。

## 文件和重建

- [逐文件清单](../data/manifests/ifs_historical_tracks_files_2026-11-10.csv)
  记录对象键、下载 URL、字节范围、SHA-256、解析状态与失败原因。
- [逐案例补充表](../data/catalogs/ifs_case_supplement_2026-11-10.csv)
  保留冻结 case ID、两来源的状态、选用来源、原始文件和支持端点时间。
- [汇总清单](../data/manifests/ifs_historical_tracks_2026-11-10.json)
  记录覆盖、同点对比和派生产物校验和。
- 原始下载保存在 `data/raw/baselines/ifs/historical_tracks/`。
  `data/interim/ifs_historical_tracks/` 下的 `cohort_tracks.csv.gz` 含三个来源的
  已核验完整轨迹，可按选用来源与消息 ID 连接逐案例表。
  `matched_case_endpoints.csv.gz` 含合并后可用案例的实际支持点。
  两份 `*_storm_links.csv.gz` 保留身份、重复和冲突审计。
  原始下载与大型派生文件保存在本地数据根目录。

重建使用既有的 `ai-weather-eval` Conda 环境、ecCodes、curl，以及原上传 BUFR
生成的冻结 `data/interim/ifs_bufr/cohort_tracks.csv.gz`。

```bash
conda run --name ai-weather-eval python scripts/download_ifs_history.py --source aws --workers 2
conda run --name ai-weather-eval python scripts/download_ifs_history.py --source ncar --workers 3
conda run --name ai-weather-eval python scripts/audit_ifs_history.py
```

下载器校验并复用已保存对象，失败对象可重试。审计默认写本地派生表。
在核对结果后加 `--publish` 可更新仓库内版本化的清单和逐案例表。
