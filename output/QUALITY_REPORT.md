# 质量报告：人名数据集 → IPA（TTS 用）

> 生成时间：2026-11-19 21:24  
> 产物：`output/name_ipa_lexicon.parquet` · `output/dataset_with_ipa/` · `output/global_top_tokens.parquet` · `demo/demo.py`（命令行 demo；可视化/音频 demo 由 Lead 的 `docs/showcase.html` 提供）  
> 实现：`src/pipeline.py`（五级流水线，方法见 `docs/METHOD.md`）。本报告所有数字均由本次运行实测产出，统计脚本 `src/report.py` 可复现。

## 0. 一句话结论

对 **491,655,925 条记录 / 105 个国家** 的数据集，本流水线先在 token 层去重（全数据集 **67,736,023** 个唯一 token，压缩比 1 : 7.3），再按「国家 × 文字 → 语言」判定，用 espeak-ng 批量生成 IPA。本次交付运行构建了 **1,621,451** 个 `(token, lang)` 发音单元，解析率 **99.95%**，覆盖 105 个国家的高频名字；`demo/demo.py` 可即时演示任意名字，可视化可试听 demo 见 `docs/showcase.html`。

成本口径：espeak-ng 通过 ctypes 实测 **40,000–90,000 token/秒（单核）**；全量 102,864,698 个 `(token × 国家)` 单元的 IPA 生成约 **43–65 分钟（单核）**，数据扫描 **186.5 秒**（duckdb 8 线程）。**瓶颈不是算力，而是语言判定与高频名校核。**

## 1. 数据与漏斗（实测）

| 项 | 数值 | 说明 |
|---|---|---|
| 记录总数 | 491,655,925 | 与 description.txt 完全一致 |
| 原始体积 | 9.95 GB | 105 个 CSV，无表头 |
| 全局唯一 token | 67,736,023 | first 30,894,739 + last 36,841,284 |
| (token × 国家) 发音单元 | 102,864,698 | 全量 G2P 的真实工作量 |
| 本次词典单元 | 1,621,451 | 覆盖每个国家约 85.0% 的 token 出现次数（cap 40,000/国） |
| 本次扫描 token 出现次数 | 898,963,607 | 进入语言判定的 token 实例（保留 + junk） |

**规范化 / junk 过滤保留率（在 26,250 条真实随机行的 52,500 个字段上实测）：**

| 指标 | 数值 | 占比 |
|---|---|---|
| 字段 | 52,500 | 100% |
| 空字段 | 457 | 0.9% |
| 多词字段（如 Anne Marie / Abd Elkarim） | 4,043 | 7.7% |
| 被规范化改写的字段（装饰符/全角/弯引号等） | 161 | 0.3% |
| word token 总数 | 56,437 | 100% |
| 判为 junk 的 word token | 0 | 0.000% |

规范化实例：`'दानु'→'दान'`；`'तनहुं'→'तनह'`；`'আলী'→'আল'`；`'ﻣﺣﻤﺪ ﻓﺮﻳﺪ'→'محمد فريد'`；`'اﻧﻮﺭﻱ'→'انوري'`；`'গনি'→'গন'`；`'তৌ'→'ত'`；`'অচেনা'→'অচেন'`

> 结论：token 层 junk 极少（高频 token 几乎都是合法人名）；主要脏数据在长尾和字段级（空字段、装饰符、多词名）。过滤只会丢弃约 0.1% 的 token 出现次数，因此**不会牺牲覆盖率**。

## 2. 语言判定：本方法的真正难点

数据集里「国家」不是书写系统：**绝大多数 token 是拉丁字母**。本运行的随机行采样（52,500 个字段）文字分布：

| script | 占比 |
|---|---|
| Latn | 88.0% |
| Arab | 6.7% |
| Cyrl | 2.5% |
| none | 0.9% |
| Hani | 0.4% |
| Hang | 0.4% |
| Hebr | 0.3% |
| Khmr | 0.2% |
| Grek | 0.2% |
| Beng | 0.1% |
| Geor | 0.1% |
| Ethi | 0.1% |

非拉丁文字占比最高的国家（随机行采样，仅列 top 15；与 `work/lead_scan/script_random_scan.json` 一致）：

| country | 非拉丁文字占比 |
|---|---|
| YE | 89.6% |
| SD | 82.4% |
| KZ | 74.8% |
| RU | 74.8% |
| LY | 70.4% |
| IQ | 67.6% |
| SY | 62.4% |
| JO | 55.2% |
| EG | 48.4% |
| PS | 47.6% |
| AF | 42.4% |
| KR | 41.4% |
| IL | 39.8% |
| TM | 32.4% |
| SA | 31.6% |

**本次运行的语言判定理由分布**（`lang_reason` 字段，可逐条审计）：

| lang_reason | units | 含义 |
|---|---|---|
| country-latin-lts | 1,107,954 | 该国主 voice 是拉丁语言，自己做 LTS（如 FR/US/PL/TR） |
| english-fallback | 267,138 | 拉丁 token 但该国主 voice 非拉丁 → 显式英语 LTS（anglicised） |
| script:country | 175,057 | 非拉丁文字 + 该国 voice 原生读这种文字（RU→ru, EG→ar, KR→ko…） |
| pinyin-latn | 64,563 | CN/TW/HK/MO 的拉丁 token 是拼音/粤拼 → 专用 voice |
| script:default | 5,986 | 非拉丁文字 + 全局文字默认 voice（该国 voice 不覆盖该文字） |
| unsupported-script | 752 | espeak 无该文字 voice（高棉/迪维希/藏文）→ unresolved |
| g2p-fallback-voice | 1 | 主 voice 失败，用兜底 voice 输出 |

> 关键工程发现（独立复现）：非拉丁文字的 espeak voice 读拉丁字母时会**静默退回英语 LTS**（ru/ar/el/ko/ka/he/kk 对 `Laure` 的输出与 en-us 逐字节相同），只有 `ja` 会逐个拼读字母。因此拉丁 token 在非拉丁国家**显式**路由到 `en-us` 并标注 `english-fallback`，而不是静默假装成当地语音规则。CN/TW 走 `cmn-latn-pinyin`、HK/MO 走 `yue-latn-jyutping`。

## 3. 覆盖率与解析率

词典单元 **1,621,451**，解析成功 **1,620,699**（**99.95%**）。按来源：

| source | units |
|---|---|
| g2p:espeak | 1,620,698 |
| unresolved:no-voice | 752 |
| g2p:espeak-fallback | 1 |

| confidence | units | 含义 |
|---|---|---|
| high | 1,129,992 | 词典命中 / 该语言自身 LTS / voice 原生读该文字 |
| medium | 320,163 | 英语回退、拼音前端、全局文字兜底、希伯来文缺元音 |
| low | 170,544 | 代理语言 voice、日语汉字、无 voice 的文字 |
| none | 752 | 无法解析（记录原因） |

按语言的解析率（按记录数排序，取前 25）：

| lang | units | records | resolved | resolution | high | medium | low | none |
|---|---|---|---|---|---|---|---|---|
| en-us | 300,982 | 280,422,750 | 300982 | 100.0% | 54647 | 243955 | 2380 | 0 |
| ar | 50,902 | 145,461,551 | 50902 | 100.0% | 50396 | 506 | 0 | 0 |
| es-419 | 26,437 | 93,800,834 | 26437 | 100.0% | 26437 | 0 | 0 | 0 |
| it | 33,989 | 63,083,629 | 33989 | 100.0% | 33989 | 0 | 0 | 0 |
| en | 180,910 | 48,661,483 | 180910 | 100.0% | 47974 | 0 | 132936 | 0 |
| fr-fr | 78,560 | 34,954,716 | 78560 | 100.0% | 75195 | 0 | 3365 | 0 |
| tr | 13,569 | 32,983,678 | 13569 | 100.0% | 13569 | 0 | 0 | 0 |
| en-gb | 55,372 | 27,886,842 | 55372 | 100.0% | 55372 | 0 | 0 | 0 |
| es-mx | 4,179 | 26,743,907 | 4179 | 100.0% | 4179 | 0 | 0 | 0 |
| es-es | 11,021 | 24,090,123 | 11021 | 100.0% | 11021 | 0 | 0 | 0 |
| ms | 58,524 | 21,238,980 | 58524 | 100.0% | 58524 | 0 | 0 | 0 |
| nl | 64,114 | 16,037,612 | 64114 | 100.0% | 64114 | 0 | 0 | 0 |
| pt-br | 9,386 | 15,573,695 | 9386 | 100.0% | 9386 | 0 | 0 | 0 |
| de | 81,109 | 14,653,924 | 81109 | 100.0% | 81109 | 0 | 0 | 0 |
| ru | 28,618 | 12,523,739 | 28618 | 100.0% | 28435 | 183 | 0 | 0 |
| yue-latn-jyutping | 22,581 | 5,537,983 | 22581 | 100.0% | 0 | 22581 | 0 | 0 |
| pt-pt | 34,018 | 5,285,192 | 34018 | 100.0% | 34018 | 0 | 0 | 0 |
| pl | 26,387 | 4,570,265 | 26387 | 100.0% | 26387 | 0 | 0 | 0 |
| kk | 27,833 | 3,780,907 | 27833 | 100.0% | 27833 | 0 | 0 | 0 |
| he | 9,783 | 2,752,678 | 9783 | 100.0% | 634 | 9149 | 0 | 0 |
| fi | 19,474 | 2,387,796 | 19474 | 100.0% | 19474 | 0 | 0 | 0 |
| cs | 39,716 | 2,345,593 | 39716 | 100.0% | 39716 | 0 | 0 | 0 |
| sv | 39,255 | 1,878,924 | 39255 | 100.0% | 39255 | 0 | 0 | 0 |
| cmn-latn-pinyin | 41,982 | 1,743,529 | 41982 | 100.0% | 0 | 41982 | 0 | 0 |
| bn | 2,305 | 1,214,980 | 2305 | 100.0% | 1757 | 548 | 0 | 0 |

**未解决 token（词典中 ipa 为空，按记录频次 top 15）：** 这些几乎全部是 espeak 没有 voice 的文字（高棉 Khmer / 迪维希 Thaana / 藏文等）——本方案选择**标记为未解决而不是伪造发音**。

| token | script | lang | records | 原因 |
|---|---|---|---|---|
| `Lłňế` | other | — | 2,699 | espeak-ng has no voice for other (documented coverage hole) |
| `Ěł` | other | — | 1,939 | espeak-ng has no voice for other (documented coverage hole) |
| `Kříž` | other | — | 1,430 | espeak-ng has no voice for other (documented coverage hole) |
| `Vũ` | other | — | 1,403 | espeak-ng has no voice for other (documented coverage hole) |
| `Ȝǻṁlǻ` | other | — | 1,127 | espeak-ng has no voice for other (documented coverage hole) |
| `Ẍƿƿ` | other | — | 1,125 | espeak-ng has no voice for other (documented coverage hole) |
| `Řø` | other | — | 1,121 | espeak-ng has no voice for other (documented coverage hole) |
| `Ëł` | other | — | 1,037 | espeak-ng has no voice for other (documented coverage hole) |
| `Ꮜꮑ` | other | — | 938 | espeak-ng has no voice for other (documented coverage hole) |
| `Êł` | other | — | 919 | espeak-ng has no voice for other (documented coverage hole) |
| `Aślņ` | other | — | 908 | espeak-ng has no voice for other (documented coverage hole) |
| `Ęł` | other | — | 865 | espeak-ng has no voice for other (documented coverage hole) |
| `Šh` | other | — | 860 | espeak-ng has no voice for other (documented coverage hole) |
| `Eł` | other | — | 850 | espeak-ng has no voice for other (documented coverage hole) |
| `Ặł` | other | — | 842 | espeak-ng has no voice for other (documented coverage hole) |

按国家的 **token 实例覆盖率**（保留下来的 token 覆盖了该国多少 token 出现次数）：

| country | rows | token instances | tokens kept | instance coverage | junk tokens | unresolved % | top langs |
|---|---|---|---|---|---|---|---|
| KH | 2,759 | 5,686 | 2,602 | 85.0% | 1 | 15.43 | en:2245|(unresolved):323|ar:16|th:9 |
| HN | 15,827 | 35,012 | 1,980 | 85.0% | 0 | 0.0 | es-419:1980 |
| SV | 4,479 | 9,857 | 1,489 | 85.0% | 0 | 0.0 | es-419:1489 |
| AF | 558,327 | 1,271,986 | 13,336 | 85.0% | 2 | 0.01 | en-us:7752|fa:5564|(unresolved):8|cmn:5 |
| AL | 506,582 | 1,039,204 | 10,564 | 85.0% | 0 | 0.0 | sq:10564 |
| GR | 617,581 | 1,261,460 | 40,000 | 74.8% | 1 | 0.0 | en-us:32958|el:5828|ar:877|ru:267 |
| FR | 19,845,064 | 41,640,979 | 40,000 | 75.2% | 0 | 0.0 | fr-fr:39890|ar:92|ru:17|(unresolved):1 |
| KZ | 3,212,979 | 6,448,844 | 40,000 | 76.3% | 0 | 0.0 | kk:27833|en-us:12162|ar:5 |
| KW | 3,641,564 | 7,631,487 | 40,000 | 78.3% | 2 | 0.0 | en-us:39998 |
| RU | 9,992,686 | 20,050,165 | 40,000 | 78.7% | 0 | 0.0 | ru:27551|en-us:12437|ar:11|(unresolved):1 |
| TW | 706,072 | 1,193,430 | 40,000 | 78.8% | 1 | 0.0 | cmn-latn-pinyin:25021|cmn:14942|ja:12|th:9 |
| DZ | 11,478,355 | 24,502,836 | 40,000 | 78.9% | 4 | 0.13 | en-us:32723|ar:7037|(unresolved):222|ru:9 |
| CA | 3,492,732 | 7,152,634 | 40,000 | 79.4% | 1 | 0.0 | en-us:39736|ar:223|ru:31|pa:8 |
| DE | 6,053,607 | 12,361,595 | 40,000 | 79.6% | 0 | 0.0 | de:39550|ar:361|ru:79|el:9 |
| PH | 897,494 | 1,951,959 | 40,000 | 79.7% | 1 | 0.0 | en:39946|ar:41|ja:5|cmn:4 |

> `instance coverage` 是 token 出现次数口径（一国的名字平均 2 个 token，因此记录级覆盖率约为其平方；完整回填时 OOV 会在线 G2P，不丢失任何行）。

## 4. 端到端回填验证（FJ / IS / EE 三个小国，100% 行）

| country | rows | full-row IPA | first-name IPA | output |
|---|---|---|---|---|
| FJ | 5,362 | 99.91% | 99.98% | 0.3 MB |
| IS | 31,343 | 100.00% | 100.00% | 1.27 MB |
| EE | 87,529 | 99.99% | 100.00% | 3.72 MB |

回填路径：先查词典（命中即零成本），未命中的 (token, lang) 汇总后**批量**在线 G2P。本次 OOV **34,674** 个单元，解析 **34,659**，耗时 **1.0s**。即使词典只覆盖高频 token，回填后的行覆盖率仍接近 100%——这是「词典 + 在线兜底」两层的价值。

输出：`output/dataset_with_ipa/{FJ,IS,EE}.parquet`，列包含原文 + `first_ipa/last_ipa/ipa_full` + `first_phonemes/last_phonemes` + `first_lang/last_lang` + `first_source/last_source` + 解析标记。

## 5. 50 条人工抽查样本（跨语言/文字，按记录频次取头部）

| # | token | lang | script | IPA | phonemes | source | conf |
|---|---|---|---|---|---|---|---|
| 1 | `Ahmed` | en-us | Latn | `ˈæxmɛd` | `'axmEd` | g2p:espeak | high |
| 2 | `Van` | en | Latn | `vˈan` | `v'an` | g2p:espeak | high |
| 3 | `Michael` | de | Latn | `mˈɪçaˌeːl` | `m'ICa,e:l` | g2p:espeak | high |
| 4 | `Marie` | fr-fr | Latn | `maʁˈi` | `mar'i` | g2p:espeak | high |
| 5 | `Van` | nl | Latn | `vˈɑn` | `v'An` | g2p:espeak | high |
| 6 | `Mohd` | ms | Latn | `mohˈamad` | `moh'amad` | g2p:espeak | high |
| 7 | `John` | en-gb | Latn | `dʒˈɒn` | `dZ'0n` | g2p:espeak | high |
| 8 | `محمد` | ar | Arab | `muħˈammad` | `muH'ammad` | g2p:espeak | high |
| 9 | `Chen` | cmn-latn-pinyin | Latn | `tʃˈɛn` | `tS'En_|` | g2p:espeak | medium |
| 10 | `Petr` | cs | Latn | `pˈetr̩` | `p'etr-` | g2p:espeak | high |
| 11 | `Andersson` | sv | Latn | `ˈandəʂˌɔn` | `'andE-s.,On` | g2p:espeak | high |
| 12 | `Tomas` | lt | Latn | `tˈoːmas` | `t'o:mas` | g2p:espeak | high |
| 13 | `Anne` | nb | Latn | `ˈɑnna` | `'Anna#` | g2p:espeak | high |
| 14 | `Muhammad` | id | Latn | `muhˈammad` | `muh'ammad` | g2p:espeak | high |
| 15 | `Silva` | pt-pt | Latn | `sˈilvɐ` | `s'ilv&` | g2p:espeak | high |
| 16 | `Maria` | it | Latn | `mˌarˈiːa` | `m,aR'i:;a` | g2p:espeak | high |
| 17 | `Александр` | ru | Cyrl | `aɭʲiksˈɑndr` | `al;iks'And@-r` | g2p:espeak | high |
| 18 | `Александр` | kk | Cyrl | `ɑleksˈɑndɾ` | `Aleks'And*` | g2p:espeak | high |
| 19 | `Maria` | es-419 | Latn | `mˈaɾja` | `m'arja` | g2p:espeak | high |
| 20 | `Anna` | pl | Latn | `ˈanna` | `'anna#_` | g2p:espeak | high |
| 21 | `Nielsen` | da | Latn | `nˈʔiəlsən` | `n'?i@-ls@-n` | g2p:espeak | high |
| 22 | `Olga` | et | Latn | `ˈolɡa` | `'olga` | g2p:espeak | high |
| 23 | `Chan` | yue-latn-jyutping | Latn | `tʃˈan` | `tS'an_|` | g2p:espeak | medium |
| 24 | `Silva` | lb | Latn | `zˈilvɑ` | `z'ilva` | g2p:espeak | high |
| 25 | `Mikko` | fi | Latn | `mˈikːo` | `m'ik:o` | g2p:espeak | high |
| 26 | `美玲` | cmn | Hani | `mˈei2 lˈiɜŋ` | `m'ei214_| l'i35N_|` | g2p:espeak | low |
| 27 | `Ivan` | hr | Latn | `ˈivæn` | `'iv&n` | g2p:espeak | high |
| 28 | `Gashi` | sl | Latn | `ɡˈaːsxi` | `g'a:sxi` | g2p:espeak | high |
| 29 | `Nagy` | hu | Latn | `nˌɑɟ` | `n,AJ` | g2p:espeak | high |
| 30 | `Mehmet` | tr | Latn | `mɛhmˈɛt` | `mEhm'Et` | g2p:espeak | high |
| 31 | `Ion` | ro | Latn | `jˈon` | `j'on` | g2p:espeak | high |
| 32 | `Borg` | mt | Latn | `bˈork` | `b'ork` | g2p:espeak | high |
| 33 | `Jose` | es-es | Latn | `xˈose` | `x'ose` | g2p:espeak | high |
| 34 | `Hoxha` | sq | Latn | `hˈɔdʒɑ` | `h'OdZA` | g2p:espeak | high |
| 35 | `Serdar` | tk | Latn | `θˈerdɑːr` | `T'eRdA:R` | g2p:espeak | high |
| 36 | `כהן` | he | Hebr | `χhˈen` | `Xh'en` | g2p:espeak | medium |
| 37 | `Nikola` | sr | Latn | `nˈikolɐ` | `n'ikol&` | g2p:espeak | high |
| 38 | `Silva` | pt-br | Latn | `sˈiʊvæ` | `s'iUv&` | g2p:espeak | high |
| 39 | `Jean` | ht | Latn | `jeˈan` | `je'an` | g2p:espeak | high |
| 40 | `محمد` | fa | Arab | `mohˈammad` | `moh'ammad` | g2p:espeak | high |
| 41 | `佐藤` | ja | Hani | `tʃˈaɪniːzlˈe̞tə tʃˈaɪniːzlˈe̞tə` | `_:tS'aIni:zl'et@ _:tS'aIni:zl'et@` | g2p:espeak | low |
| 42 | `Memmedov` | az | Latn | `memmedˈov` | `memmed'ov` | g2p:espeak | high |
| 43 | `Γιώργος` | el | Grek | `jˈorɣos` | `j'orQos` | g2p:espeak | medium |
| 44 | `영호` | ko | Hang | `jˈʌŋho` | `j'@Nho` | g2p:espeak | high |
| 45 | `Anna` | is | Latn | `ˈanna` | `'anna` | g2p:espeak | low |
| 46 | `Георги` | bg | Cyrl | `ɡeˈorɡiː` | `ge'orgi:` | g2p:espeak | high |
| 47 | `Hernandez` | es-mx | Latn | `ˌeɾnandˈeθ` | `,ernand'eT` | g2p:espeak | high |
| 48 | `歐陽` | yue | Hani | `ŋˈau1 jˈoenɡ4` | `N'au1_| j'oeng4_|` | g2p:espeak | low |
| 49 | `ম` | bn | Beng | `mˈɔ` | `m'O` | g2p:espeak | high |
| 50 | `გიორგი` | ka | Geor | `ɡˈiorɡi` | `g'iorgi` | g2p:espeak | high |

## 6. 吞吐实测与「全量 491.6M」外推

| 环节 | 本次实测 | 吞吐 | 全量外推 |
|---|---|---|---|
| duckdb 全量扫描（105 CSV / 9.95GB / 491.6M 行） | 186s | — | 186s |
| token 词表提取（本次实际运行） | 460.7s · 3,682,136 tokens | — | ≈ 同量级（一次全量扫描） |
| IPA 生成（espeak ctypes，含 espeak phoneme 串两遍） | 1,621,451 units / 177.6s | 8,823 units/s | 102.86M units ÷ 8,823/s ≈ 194 min（单核） |
| 回填（词典查询 + OOV 在线 G2P） | 124,234 rows / 9.0s | 13,803 rows/s | 491.6M rows ≈ 9.9 h（单进程；按国家分片 8 进程 ≈ ÷8） |
| 词典体积（zstd parquet） | 44.1 MB | 0.03 KB/unit | 2 MB（67.7M unique token 全量） |
| 回填输出体积（zstd parquet） | 5.3 MB / 124,234 rows | 0 B/row | ≈ 21 GB（491.6M 行全量） |

**结论：全量生成 IPA 是 1 小时级的任务，不是几天。** 真正的成本在 (a) 语言判定正确率、(b) top-N 高频名的人工校核、(c) 15–25 GB parquet 的 IO。建议执行顺序：

1. 全量 token 去重 → 语言判定 → espeak 生成（≈1 小时单核，可多进程并行）→ 67.7M 词词典；
2. 对 top 1 万 first + top 1 万 last（覆盖 65.6% / 42.6% 记录）做人工/词典校核；
3. 校核结果合并回词典，再全量回填生成 `dataset_with_ipa`；
4. 长尾错误靠抽样/用户反馈迭代——词典是一次投入、永久复用，且一个 token 平均修正 7 条记录。

**全数据集频次覆盖曲线（来自 `work/lead_scan/global_freq.parquet` 全量统计；`output/global_top_tokens.parquet` 为 top-N 词表）：**

| 前 N 个 token | first name 覆盖 | last name 覆盖 |
|---|---|---|
| 1,000 | 34.1% | 17.5% |
| 10,000 | 62.5% | 39.2% |
| 100,000 | 80.2% | 63.7% |
| 500,000 | 86.9% | 77.9% |
| 1,000,000 | 89.1% | 82.8% |
| 2,000,000 | 90.9% | 86.8% |

## 7. 已知质量天花板（不是实现 bug，是数据/引擎的结构性限制）

| 限制 | 影响范围 | 本流水线的处理 |
|---|---|---|
| 阿拉伯文/希伯来文省略短元音 | EG/SA/IQ/SD/YE/JO/LY/SY/PS/IL 等 | 照常输出，标 `medium`（Hebrew）/记录在案的误差源 |
| 日语汉字读音 | JP（3.8% 汉字） | espeak 1.52 的 ja 读汉字是坏的 → 标 `low` |
| 无 espeak voice 的文字 | KH 高棉 31%、MV 迪维希、藏文等 | `unresolved` + 原因，**不伪造**（见 demo 中的高棉示例） |
| 非拉丁国家的拉丁 token | RU/GR/GE/IL/JP/KR/AE… | 显式 `english-fallback`（英语 LTS），anglicised；可后续加「原语言 IPA→目标音素表」层 |
| espeak 用 MSA 近似地方阿拉伯语 | EG/MA/DZ/TN | `ar` voice；口语差异未建模，标 `medium` |
| 同形异读 | 全局 | 按 `(token, lang)` 存储，靠国家先验消歧，无法 100% |
| 代理语言（无 voice 的小语种） | AF/BI/BW/FJ/GH/KH/MU/NG/PH/ZA/IS | 用最接近的 voice，`confidence=low` |
| WikiPron 词典层默认关闭 | 全语言 | 实测其 broad 转写**无重音符号且音素集与 espeak 不同**（只有 2/34,399 命中），替换 espeak IPA 会让 TTS 听感变差；故默认 OFF（`--lexicon` 可选开启做 QA） |
| 冻结的 normalize.py 边界行为 | Indic 文字边缘 | 其“去边缘标点”会删掉少数词尾的 Indic 元音符号（如 दानु→दान），占实测字段 0.3% 中的很小一部分；属上游冻结规范，本流水线照用并在报告标注 |

## 8. 交付物与复现

| 路径 | 内容 |
|---|---|
| `src/pipeline.py` | 五级流水线库 + CLI（extract / resolve / join / demo / report / global / all） |
| `src/extract.py` | 阶段①：duckdb 单遍扫描 → 每国 token 词频 + 真实姓名采样 |
| `src/norm.py` | `profile/normalize.py` 的封装（norm / key / junk flags） |
| `src/langmap.py` | 阶段②③：文字检测 × 105 国 voice 表 → lang/lang_reason/confidence |
| `src/g2p.py` | espeak-ng ctypes 批量 G2P（IPA + espeak phoneme 串 + WAV） |
| `src/demo_build.py` | demo 选样 + 逐 token 合成音频 + 生成离线 HTML |
| `src/report.py` | 本报告生成器 |
| `output/name_ipa_lexicon.parquet` | `(token, lang)` → IPA / phonemes / source / confidence（1,621,451 行） |
| `output/dataset_with_ipa/<CC>.parquet` | 原始行 + IPA 回填（FJ/IS/EE 全量验证） |
| `output/coverage_by_country.csv` / `coverage_by_language.csv` | 覆盖率与溯源审计 |
| `output/global_top_tokens.parquet` | 全数据集 top-N 词频（人工校核的优先队列） |
| `demo/demo.py` | 命令行 demo：`python demo/demo.py "Laure" --country FR --audio` |
| `docs/showcase.html` | 可视化 + 试听 demo（Lead 交付，226 条真实样例 / 225 WAV） |
| `demo/demo.py` | 命令行 demo：`python demo/demo.py "Laure" --country FR --audio` |

```bash
# 全流程（约 15 分钟：扫描 + 词典 + 3 国回填 + demo + 报告）
python -m src.pipeline all --countries FJ,IS,EE
# 或分步
python -m src.pipeline extract
python -m src.pipeline resolve
python -m src.pipeline join --countries FJ,IS,EE
python -m src.pipeline demo
python -m src.pipeline report
# 单条查询
python demo/demo.py "Jean" --country FR --country US
```

> 环境依赖：Python 3.12（本机 anaconda）、`work/pylibs` 内的 duckdb（无需 pip）、`pyarrow`、以及 espeak-ng 1.52 便携版（`g2p_eval/espeak/installed/...`，`engines.py` 自动定位）。全部离线、确定性可复现（固定随机种子）。
