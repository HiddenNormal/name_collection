# name_collection — 人名数据集 → IPA 发音

把 [philipperemy/name-dataset](https://github.com/philipperemy/name-dataset) 的
**491,655,925 条记录 / 105 个国家**变成可用的 **IPA 发音**，用于 **TTS 朗读人名**。

> 目标：一套**可复现的方法 + 一个能跑的 demo**，不是把 4.9 亿行全量导出的成品。

⚠️ **原始数据集不进 Git**（3.3 GB，且源自第三方泄露数据）。克隆后运行 `scripts/bootstrap.py` 自动下载。

---

## 快速开始（全新 clone）

```powershell
# 0) 先看缺什么
python scripts/bootstrap.py --status

# 1) 自动下载并解压数据集（3.3GB，Google Drive）+ WikiPron 词典 + 可选 espeak-ng
python scripts/bootstrap.py

# 2) 验证环境
python src/pipeline.py info

# 3) 看 demo（最快，无需数据）
#    直接浏览器打开 docs/showcase.html  <- 静态离线，含音频
```

> 数据集缺失时，`pipeline.py` **会自动触发下载**；想关掉就设
> `NAME_COLLECTION_AUTO_DOWNLOAD=0`。
> 离线/镜像：`NAME_DATASET_ZIP=D:\mirrors\name_dataset.zip` 或
> `NAME_DATASET_ZIP_URL=https://my.mirror/name_dataset.zip`。

### 跑命令行 demo
```bash
python demo/demo.py "Laure" --country FR
python demo/demo.py --file names.txt --audio      # 批量 + 合成 WAV
python demo/demo.py --examples                    # 24 个脚本化用例
```

### 跑 pipeline（子集验证）
```bash
python src/pipeline.py all --countries FJ,IS,EE   # 子集端到端
python src/pipeline.py extract                     # 全量 token 扫描（~8 分钟）
python src/pipeline.py resolve                     # 语言判定 + IPA → 词典
```

---

## 仓库里提交了什么 / 没提交什么

| 提交 ✅ | 不提交 ❌（`bootstrap.py` 下载或 `pipeline.py` 重新生成） |
|---|---|
| `src/` `scripts/` `demo/demo.py` 全部代码 | `external/name_dataset.zip`（3.3 GB 原始数据） |
| `docs/`（方法文档、国别语言表、showcase demo + 音频） | `work/extract/`（解压后的 10 GB CSV） |
| `profile/normalize.py`、`profile/report.md` | `profile/*.parquet`（2.3 GB 唯一 token 清单） |
| `g2p_eval/`（`EVAL_REPORT.md`、`engines.py`、`bench.csv`、`gold/`、`tools/`） | `g2p_eval/{espeak,models,lexicons,pylibs*}`（~2.2 GB） |
| `output/`（`QUALITY_REPORT.md`、覆盖率 CSV、摘要 JSON） | `output/*.parquet`、`output/dataset_with_ipa/` |

`.gitignore` 的完整清单见文件本身；一条命令即可恢复全部被忽略内容。

---

## 数据来源与隐私（重要）

- 数据集来自 **[philipperemy/name-dataset](https://github.com/philipperemy/name-dataset)**（PyPI [`names-dataset`](https://pypi.org/project/names-dataset/)，代码 MIT）。
- 上游明确说明：这批姓名是**从 Facebook 533M 用户泄露数据中抽取的**（2021 年泄露事件）。
- 所以本仓库**不提交**原始数据、解压后的 CSV、以及任何大规模姓名派生表
  （唯一 token 清单、词典 parquet、回填后的数据集全部在 `.gitignore` 里）。
- 仓库里只保留**极少量样例**用于 demo/文档（`docs/showcase.html` 的 226 条高频姓名 + 音频）。
  如果这仍然不适合你的合规要求，删掉 `docs/showcase.html`、`docs/showcase_data.json`、
  `docs/audio/` 即可，`docs/METHOD.md` 的结论不依赖它们。
- 上游对数据许可的说法是「姓名列表通常不受版权保护」（类似电话簿），但**未给明确许可证**。
  商用前请自行做法务确认；**不要再分发原始数据**。

---

## 许可证

| 组件 | 许可证 | 备注 |
|---|---|---|
| 本仓库自有代码 | MIT（见 `LICENSE`） | |
| espeak-ng 1.52.0 | **GPL-3.0** | 不提交，由 `bootstrap.py` 获取。分发时走独立进程边界 |
| WikiPron 词典 | **CC BY-SA** | 不提交，运行时下载；派生词典需署名 + 相同方式共享 |
| CMUdict | BSD 类 | 可自由使用 |
| 上游姓名数据 | 未明确（源自泄露） | **不要分发** |

---

## 核心结论（先看这 5 条）

1. **不要逐行算发音。** 491.6M 条记录去重后只有 **67,736,023 个唯一 token**（1 : 7.3 压缩）；
   按国家展开是约 **1.03 亿**个 `(token, 国家)` 发音单元。
2. **算力不是瓶颈。** espeak-ng 单核 **1.2–2.1 万 token/秒**，全量生成 IPA 约 **1 小时**。
3. **真正的难点是语言判定，不是多文字转写。** 数据集里 80 个国家几乎全是拉丁字母；
   实测 65% 的 token 是「拉丁语言的真本地发音」，只有约 14% 走非拉丁文字路径。
4. **最隐蔽的坑：espeak 的 native voice 会把拉丁字母静默读成英语。** 对 `ru/ar/el/ko/ka/he/kk/bg`
   等非拉丁文字 voice，输入拉丁文本时输出与 `en-us` 完全一致（`ja` 更糟，会逐字母拼读）。
   所以「用俄语 voice 读俄国人的拉丁写名」得到的是英语发音——**必须显式记录 `lang_reason`**。
   详见 `docs/METHOD.md` §3.1。
5. **质量按频率分档管理。** 前 1 万个 first name 覆盖 **65.6%** 的记录，前 100 万覆盖 **90.6%**；
   长尾 6000 万 token 只贡献 9.4%。所以「高频人工校核 + 长尾自动生成并标置信度」才是正确做法。

---

## 方法一览（五级流水线）

```
原始 CSV
  │  ① 规范化 + 过滤（profile/normalize.py）
  │      NFC、去 tatweel/音符/零宽字符、去边缘装饰、剔除垃圾
  ▼
唯一 token 清单（(token, 国家) 聚合，带频次）
  │  ② 文字系统检测（Latn/Cyrl/Arab/Hang/Hani/... 共 20+ 种）
  │  ③ 语言判定：国家先验 × 文字约束 → espeak voice
  ▼
  │  ④ IPA 解析级联：词典（WikiPron/CMUdict）→ espeak G2P → 兜底 → 标记未解决
  ▼
name→IPA 词典（带 source / confidence / lang_reason 溯源）
  │  ⑤ 回填
  ▼
带 IPA 的原数据集（按国家分片 parquet）
```

完整说明见 **[`docs/METHOD.md`](docs/METHOD.md)**（含实测数据、选型依据、质量天花板、TTS 落地建议）。

---

## 实测结果

**全 105 国抽样验证**（每国每字段取频率最高的 3000 个 token，共 624,790 个）：

| 指标 | 结果 |
|---|---|
| 成功产出 IPA | **99.78%**（623,445 / 624,790） |
| 未解决 | 1,345（几乎全是高棉文 —— espeak 无 `km` voice） |
| G2P 吞吐 | **11,689 token/秒** |
| 最低国家 | KH 69.5%（高棉文）；其余均 ≥ 98.9% |

**已生成的词典**（`output/name_ipa_lexicon.parquet`）：**1,621,451 个 `(token, lang)` 单元**，
来自全 105 国、覆盖每国约 85% 的 token 出现次数，解析率 **99.95%**。
带 `ipa` + `phonemes`（espeak 音素串）+ `source` + `confidence` + `lang_reason` + 频次/国家数。

**3 国端到端回填验证**：

| 国家 | 行数 | first IPA 覆盖 | last IPA 覆盖 | 整行覆盖 | 耗时 |
|---|---|---|---|---|---|
| FJ | 5,362 | 99.98% | 99.93% | **99.91%** | — |
| IS | 31,343 | 100% | 100% | **100%** | — |
| EE | 87,529 | 100% | 100% | **99.99%** | 9.0 秒 / 124,234 行 |

---

## 目录与产物

| 路径 | 内容 |
|---|---|
| **`docs/METHOD.md`** | **方法说明（主文档，先读这个）** |
| `docs/showcase.html` | 可视化 + 试听 demo（静态离线，双击打开，226 行真实数据 + 225 个音频） |
| `docs/country_languages.csv` | 105 国 → espeak voice / 语言 / 文字（已校验每个 voice 存在） |
| `docs/espeak_voices.csv` | espeak 222 个声音清单 |
| `README.md` | 本文件（入口 + 结论摘要） |
| `profile/normalize.py` | 规范化实现（`normalize_token` / `is_junk` / `normalize_key`） |
| `profile/report.md` | 数据画像 + 规范化 spec |
| `profile/unique_tokens.parquet` | **1.016 亿行**唯一 token 清单（国家 × 字段 × 归一化键，含频次/性别/文字/junk 标记） |
| `profile/country_stats.parquet` | 105 国统计（43 列：行数/空值/去重/文字/junk 率/词频 top20…） |
| `g2p_eval/EVAL_REPORT.md` | 引擎基准（27 语言 × 多引擎：PER/重音/覆盖率/**许可证**） |
| `g2p_eval/engines.py` | 统一 `ipa(name, lang)` 封装（espeak ctypes / WikiPron / Epitran / Charsiu） |
| `g2p_eval/espeak/` | espeak-ng 1.52.0 便携版（含 441 个数据文件，无需安装） |
| `src/pipeline.py` | 五级流水线实现 + CLI（`extract/resolve/join/demo/report/global/all/info`） |
| `demo/demo.py` | 命令行 demo（`--audio` 可合成 WAV） |
| **`output/name_ipa_lexicon.parquet`** | **1,621,451 个 `(token, lang) → IPA` 单元** + espeak 音素串 + 溯源 |
| `output/dataset_with_ipa/` | 回填 IPA 的数据集（FJ / IS / EE 三国，23 列） |
| `output/global_top_tokens.parquet` | 全局 200 万高频 token + 频次（选样/分档用） |
| `output/QUALITY_REPORT.md` | 覆盖率 / 质量 / 全量外推（323 行） |
| `work/lead_scan/` | Lead 的独立验证数据（`verify_tokens_top3000.parquet` 62 万行、`global_distinct.json` 等） |
| `work/extract/name_dataset/data/` | 解压后的原始 CSV（105 个，9.95 GB） |

---

## 已知限制（诚实清单）

| 限制 | 影响 | 说明 |
|---|---|---|
| 非拉丁文字国家的拉丁写名 → 英语发音 | 约 **20.6%** 的高频 token | 见上面结论 4；已用 `lang_reason=english-fallback` 标记 |
| 阿拉伯文/希伯来文省略短元音 | EG/SA/IQ/SD/YE/JO/LY/SY/PS/IL | 文字本身不写元音，元音只能猜，是最大误差源 |
| 日语汉字 | JP 汉字部分 | espeak 1.52 会拼读字母名，输出无意义；需先汉字→假名（pykakasi） |
| 无 voice 的语言 | 高棉（KH 31% 行）、迪维希（MV）、他加禄、基隆迪 | 无可用 voice，只能标未解决 |
| 泰国语 | TH 不在本数据集内 | espeak 泰语输出不是真 IPA |
| 英语 voice 读外来名 | US/CA/GB | `Xiong`→`kʃəŋ`、`Nguyen`→`nˈuːjɛn`（英语化，设计如此） |
| 长尾异常拼写 | 全局 | 自动生成必然出错，用置信度标记 |

### 已知数据/实现瑕疵（已定位，影响可量化）

- **规范化器 v1.0 曾误删印度系文字的尾随元音符**（`আলী`→`আল` 等）。已在 `profile/normalize.py` **v1.1 修复**
  （只对 Arabic/Hebrew 剥离尾随组合符，Indic 保留）。但 `profile/*.parquet` 与**已生成的词典**是用 v1.0 构建的，
  受影响面为 **315,876 个 key / 871,531 次出现 = 全部字段出现次数的 0.0886%**。
  已交付的 `output/dataset_with_ipa/{FJ,IS,EE}` **完全不受影响**（无印度系文字 token）。
  重跑 `src/pipeline.py extract && resolve`（约 10 分钟）即可清除该瑕疵。
- 阿拉伯文装饰符的**同形字**（ہ/ڪ/ے 等）仍被当作不同 key，约占阿拉伯文出现次数的 1.49%，
  需在 v2 的折叠表里补充。

**生产建议**：top 1 万 first + top 1 万 last（约 2 万 token，覆盖 65.6% / 42.6% 记录）人工校核，
其余自动生成并保留 `confidence`；词典是一次投入、永久复用的资产。

---

## 环境

- Python 3.12（`E:\Programs\anaconda3\python.exe`）；依赖：`pyarrow`、`duckdb`（在 `work/pylibs`）
- espeak-ng 1.52.0 便携版：`g2p_eval/espeak/installed/SourceDir/eSpeak NG`
  （运行时需设 `ESPEAK_DATA_PATH` 指向该目录）
- Windows 注意：**不要用命令行参数传非 ASCII 文本给 espeak**（控制台编码会破坏），
  用 `-f` 文件输入或 ctypes 调 `libespeak-ng.dll`
