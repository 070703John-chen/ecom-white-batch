---
name: ecom-white-batch
description: 电商白底图批量生产。把一批已有商品图一条命令转成 1:1 纯白交付图（入口挑片 → 抠底 → 去彩污 → 主体占位 → 纯白判定 → n/N 报告）。当用户说「批量白底」「白底图」「批量出白底主图」「白底交付」「把这批图洗成白底」「batch white background product images」时使用。只做后处理，不调用任何生图 API，不需要 API key。
license: MIT
metadata:
  version: "2.0"
  provenance: 从真实电商产线抽出的白底链（R99zp 起，历经 20 个批次、800 张候选验证）
---

# ecom-white-batch — 批量白底

**白底不是"生成"出来的，是"挑片 + 抠 + 贴"出来的。**

这条链解决的是：手上有一批图（模型出的、供应商给的、拍的），需要统一变成平台上架用的
1:1 纯白主图。每张图要经过**入口挑片 → 抠底 → 去彩污 → 主体占位 → 纯白判定**五步，
并给出一份 n/N 报告。

本 skill **不生图、不联网、不需要 API key**，纯像素处理。

---

## 一、快速开始

```bash
python scripts/white_batch.py --in <源图目录> --out <交付目录> --canvas 1200 --purge
```

```bash
pip install -r requirements.txt
```

跑完得到：交付图 + `_white_batch_report.json`（每张的状态、占比、纯白度、跳过原因）。

### 常用开关

| 开关 | 默认 | 作用 |
|---|---|---|
| `--canvas` | 1200 | 交付画布边长（正方形） |
| `--occ` | 0.60 | 主体占画布目标占比 |
| `--margin` | 0.06 | 主体最长边到画布边的留白 |
| `--max-upscale` | 2.0 | 放大倍数封顶（宁占比不够也不给糊图） |
| `--purge` | 关 | 走彩色污染擦除；**主体本身是彩色时它会拒擦，这是对的** |
| `--ref-dir` | 无 | 把主体占比对齐到竞品同名图（取景对齐） |
| `--only` / `--exclude` | 无 | 按文件名串过滤 |
| `--jobs` | 2 | 并发；8GB 内存机器不要超过 2 |
| `--no-src-gate` | 关 | 关掉入口挑片闸门（调试用，**不建议**） |

---

## 二、五步链

| 步 | 做什么 | 实现 |
|---|---|---|
| 1 | **入口挑片**：源图边缘环必须是浅色中性底 | `scripts/white_batch.py` 的 `source_ring` |
| 2 | 抠底：把与画布边缘连通的背景抠掉 | `scripts/white_matte.py` |
| 3 | 去彩污：擦掉模型编的绿植/蓝条/彩色装饰 | `scripts/white_purge.py` |
| 4 | 主体占位：裁主体 bbox + 贴方形画布 + 闭环迭代占比 | `scripts/white_fit.py` + `scripts/occupancy.py` |
| 5 | 纯白判定：四角中位数 ≥247、色偏 ≤6 | `scripts/white_batch.py` 的 `corner_stats` |

**3:4 → 1:1 怎么办**：第 4 步按"主体 bbox 裁出来再贴方形画布"做，所以 3:4 进来自动按主体
取景变 1:1，**不靠硬裁**，主体不会被切掉。

---

## 三、入口闸门（全部关键的一步）

**白底链只对"浅色中性底"的源图成立。** 不先挑片，后面全是假活。

判定：取源图外圈 4% 宽的边缘环，取中位色。

| 条件 | 阈值 | 不过就是 |
|---|---|---|
| 边缘环饱和度（max-min） | ≤ 12 | 源图底色有色，抠不掉 |
| 边缘环最暗通道 | ≥ 200 | 源图底色偏暗，是场景图不是白底候选 |

实测数据与判据来源见 `references/entry-gate.md`。

---

## 四、报告怎么读（别只看百分比）

`_white_batch_report.json` 里：

```
total / candidates / skipped / pass
pass_rate              = pass / total        （含被闸门跳过的，分母大）
candidate_pass_rate    = pass / candidates   （这才是"候选里过了多少"）
```

- **必须同时报候选数和通过数两个分母**，只报一个百分比会骗人。
- `status` 只有 `PASS` 的图可交付。
- **FAIL / SKIP 的图也会落盘**（为了能人工比对），但它们不是交付件。
- `why` 字段写了拒绝原因（背景没抠干净 / 非白像素散在多处 / 不是白底候选）。

---

## 五、机检过了 ≠ 成品能用

这是实测打过的脸：**49 张图，四角纯白机检判 48/49 PASS，人眼看却抓到整块淡紫背景没抠掉**
——四角闸门对"中间一大块有色背景"完全无感。

所以纪律是：

1. **闸门过 ≠ 成品可用。** 每张还要人眼看，放大 3 倍。
2. **机检只保证"底子是白且干净"**，不保证商品形态对、字对、构图对。
3. 白底只管底子。模型出的字必须自己擦、自己画 → 见 `references/post-white-fixes.md`。

### 交付前检查清单

- [ ] 候选 n/N 与通过 m/N 两组数字都报了
- [ ] 每张交付图人眼看过、放大 3 倍，没有残留底色、假字、明显硬伤
- [ ] 主体占画布比在 0.35~0.80，没有"商品太小"被平台机检驳回的风险
- [ ] 主体最长边没有顶到画布边（留白 ≥6%）
- [ ] 报告文件 `_white_batch_report.json` 在交付目录里

单独复核某张图用：

```bash
python scripts/check_white.py --dir <交付目录>
```

---

## 六、已知边界（别重新踩）

完整清单见 `references/known-limits.md`，最要紧的三条：

- **image-01 不接 seed**，同提示词两轮出图 0/24 相同 ⇒ 噪声是任务级全量置换，
  单轮差值不可采信，要多跑几轮看趋势。
- **image-01 纯白合格率 0/14**，靠提示词改不动，必须程序合成白底（就是本 skill 的存在理由）。
- **白色主体 + 白底抠不出来**：RMBG 的 alpha 只有 0.008~0.13，要用
  "阈值 + 轮廓填充 + 羽化"，不能只靠抠图模型。

---

## 七、参考文件

| 文件 | 什么时候读 |
|---|---|
| `references/entry-gate.md` | 调入口闸门阈值、或想知道为什么某张被跳过 |
| `references/post-white-fixes.md` | 白底做完之后的擦丝印/贴字/修图配方 |
| `references/known-limits.md` | 接到生图链、或要扩展这个 skill 时先读 |

## 八、脚本清单

| 文件 | 作用 |
|---|---|
| `scripts/white_batch.py` | 主入口，一条命令跑全链 |
| `scripts/white_matte.py` | 抠底（背景场估计 / 平场 / 连通背景 matting） |
| `scripts/white_purge.py` | 彩色污染擦除 |
| `scripts/white_fit.py` | 主体占位（bbox 裁切 + 闭环迭代占比） |
| `scripts/occupancy.py` | 主体占比/背景基色的度量尺子（与开源机检同口径） |
| `scripts/check_white.py` | 单目录白底合规硬检测（独立工具） |

MIT License.
