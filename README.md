# ecom-white-batch

把一批已有商品图，一条命令转成 1:1 纯白交付图。

```bash
python ecom-white-batch/scripts/white_batch.py --in ./raw --out ./delivered --canvas 1200 --purge
```

输出：交付图 + `_white_batch_report.json`（每张的状态、主体占比、纯白度、拒绝原因）。

**不生图、不联网、不需要 API key。**

---

## 仓库结构

```
ecom-white-batch/                 ← 这个目录就是 skill 本体
├── SKILL.md                       skill 入口（agent 加载的就是它）
├── agents/openai.yaml             Codex UI 元数据
├── references/                    知识层（入口闸门标定 / 擦丝印配方 / 已知边界）
├── scripts/                       可执行零件（见下）
├── examples/                      一对真实样例：输入 → 白底输出
└── requirements.txt
```

skill 放在**仓库子目录**而不是仓库根，是为了能被 Codex 官方 `skill-installer` 直接安装
（安装器要求 `--path` 指向一个目录名，`.` 会被判 "Invalid skill name"）。

---

## 安装

### A. 用 Codex 官方 skill-installer（推荐）

```bash
python <CODEX_HOME>/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo 070703John-chen/ecom-white-batch \
  --path ecom-white-batch
```

装完在下一轮对话里就能用 `$ecom-white-batch` 调用。

### B. 手动 clone（当普通工具用）

```bash
git clone https://github.com/070703John-chen/ecom-white-batch.git
cd ecom-white-batch
pip install -r ecom-white-batch/requirements.txt
python ecom-white-batch/scripts/selftest.py     # 装完先跑这个
```

### C. 当作 skill 目录手动安装

把 `ecom-white-batch/` 整个目录复制到 agent 的 skills 目录：

| Agent | 目录 |
|---|---|
| Codex | `~/.codex/skills/`（Windows：`%USERPROFILE%\.codex\skills\`） |
| Claude Code | `~/.claude/skills/` |
| OpenCode | `~/.config/opencode/skill/` |

目录名即 skill 名，里面必须有 `SKILL.md`。

### 自检

`scripts/selftest.py` 会现场合成 4 张测试图（不联网、不需要素材）跑完整条链：
坏底色图必须被入口闸门拒、好底色图必须产出且四角纯白。**退出码 0 = 环境可用。**

```
[OK] 依赖齐全：pillow / opencv / numpy
[OK] 合成测试图 4 张（好 2 / 该被拒 2）
[OK] 入口闸门行为正确：坏底色 2/2 全部被拒
[OK] 白底链产出：候选 2 · 通过 2
[OK] 交付图四角纯白（corner_min >= 247）
环境可用 OK
```

---

## 它解决什么

模型出的图、供应商给的图、拍的图，要统一变成平台上架用的白底主图。白底**不是生成出来的**，
是"挑片 + 抠 + 贴"出来的。这条链把五步接成一条命令：

1. **入口挑片** — 源图边缘环必须是浅色中性底，场景图直接拒掉
2. **抠底** — 把与画布边缘连通的背景抠掉
3. **去彩污** — 擦掉模型编的绿植/蓝条/彩色装饰
4. **主体占位** — 按主体 bbox 裁切贴方形画布，闭环迭代到目标占比（3:4 自动变 1:1，不硬裁）
5. **纯白判定** — 四角中位数 ≥247、色偏 ≤6

## 为什么入口闸门是全部关键

白底链只对"浅色中性底"的源图成立。不先挑片，后面全是假活：

实测 49 张 image-01 原始出图，纯白机检判 **48/49 PASS**，人眼却抓到整块淡紫背景没抠掉
——四角闸门对"中间一大块有色背景"完全无感。加上入口挑片后：
**49 → 候选 5 → 通过 4/5**，人眼看这 4 张底子是干净的。

**修的是入口，不是判据。**

## 实测数据

| 源图 | 边缘环饱和度 | 最暗通道 | 抠底结果 |
|---|---|---|---|
| G01 主图 | 4 | 244 | 抠干净，人眼可用 |
| G02 主图 | 44 | 153 | 淡紫底整块留着，机检照样放行 |
| 附图细节/场景 | 40~89 | 0~5 | 本来就不该走白底 |

## 报告怎么读

```
白底候选 10 张 (入口挑片跳过 4 张) · 纯白通过 9 / 10
```

必须同时报**候选数**和**通过数**两个分母。只有 `status=PASS` 的图可交付；
FAIL/SKIP 的图也会落盘（方便人工比对），但不是交付件。

## 单独复核

```bash
python ecom-white-batch/scripts/check_white.py --dir ./delivered
```

`examples/` 里放了一对真实样例：`input_sample.jpg`（源图）→ `output_sample.jpg`（白底交付图）。

---

## 能力边界（重要）

这个包是**白底链的后处理段**，不是整条电商图产线。

| 环节 | 在本包内 | 说明 |
|---|---|---|
| 入口挑片 / 抠底 / 去彩污 / 主体占位 / 纯白判定 | ✅ | 核心能力，就是这个包 |
| 独立白底合规检测 | ✅ | `scripts/check_white.py` |
| 环境自检 | ✅ | `scripts/selftest.py` |
| 竞品图拆解 / 提示词生成 | ❌ | 属于上游拆解层 |
| 生图（image-01 等） | ❌ | 本包**不调任何 API、不需要 key** |
| 整图当参考 / 多件阵列排版 | ❌ | 属于生图层 |
| 闸门判定 / 修复闭环 / 选优交付 | ❌ | 属于评价层 |
| 贴字 / 结构修复 | ❌ | 口径写在 `references/post-white-fixes.md`，但**代码不含贴字器** |

⇒ 在另一台电脑上装好这个 skill，你能复现的是「**把一批已有图洗成 1:1 纯白交付图**」这一步，
且**对同一批源图，结果与原机器逐张一致**（已用克隆件复现验证）。
**不能**复现的是「从竞品图一路生成到 13 张可上架主图」——那一段在生图层和评价层，不在这个包里。

---

## 依赖

Python ≥ 3.9，`pillow` / `opencv-python` / `numpy`。8GB 内存的机器并发别超过 2
（`--jobs 2`）。无 GUI 的服务器/CI 环境把 `opencv-python` 换成 `opencv-python-headless`。

## 已知边界

- **闸门过 ≠ 成品可用**，每张还要人眼看、放大 3 倍。
- image-01 纯白合格率 0/14，靠提示词改不动，必须程序合成白底。
- 白色主体 + 白底抠不出来（RMBG alpha 只有 0.008~0.13），要靠阈值 + 轮廓填充 + 羽化。
- 白底只管底子；模型出的字必须自己擦、自己画（见 `ecom-white-batch/references/post-white-fixes.md`）。

## 来源与许可

从真实电商产线抽出并逐轮验证（20 个批次、800 张候选）。
`ecom-white-batch/scripts/occupancy.py` 的度量口径搬运自电商上架机检开源脚本
`platform-compliance/scripts/check_listing.py` 中的 `bg_stats()` / `subject_bbox()` /
`border_ring()` / `has_border()` / `ratio_ok()` 五个函数（逐字保留算法，未改写），
以便本包不依赖任何外部绝对路径即可运行。

MIT License.
