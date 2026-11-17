# -*- coding: utf-8 -*-
"""selftest.py — 一条命令证明"这台机器装好了"。

用法
    python scripts/selftest.py

做什么
    1. 用 PIL 现场合成 4 张测试图（不依赖任何外部素材）：
         2 张浅色中性底 + 主体   -> 应当通过入口闸门
         1 张淡紫底 + 主体       -> 应当被入口闸门拒（ring_sat > 12）
         1 张深色底 + 主体       -> 应当被入口闸门拒（ring_min < 200）
    2. 跑完整条白底链（子进程调用 white_batch.py，等价于真实使用）
    3. 断言：坏图 100% 被拒 · 好图能产出 · 产出四角纯白

退出码
    0 = 环境可用
    1 = 环境不可用（缺依赖 / 链路跑不通 / 闸门行为异常）

不联网、不需要 API key、不写仓库目录（临时目录用完即删）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _out(text):
    """终端可能是 GBK/其它编码，别让中文把脚本打断。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(text)


def _make_samples(d):
    """合成 4 张测试图。返回 (好图路径列表, 坏图路径列表)。"""
    from PIL import Image, ImageDraw

    good, bad = [], []

    def subject(dr):
        dr.ellipse([220, 300, 804, 700], fill=(72, 76, 84))
        dr.rectangle([430, 250, 594, 760], fill=(150, 154, 160))

    for name in ("good_1.jpg", "good_2.jpg"):
        im = Image.new("RGB", (1024, 1024), (255, 255, 255))
        subject(ImageDraw.Draw(im))
        p = os.path.join(d, name)
        im.save(p, "JPEG", quality=95)
        good.append(p)

    # 淡紫底：ring_sat = 245-228 = 17 > 12 => 必须被拒
    im = Image.new("RGB", (1024, 1024), (240, 228, 245))
    subject(ImageDraw.Draw(im))
    p = os.path.join(d, "tinted_bg.jpg")
    im.save(p, "JPEG", quality=95)
    bad.append(p)

    # 深色底：ring_min = 60 < 200 => 必须被拒
    im = Image.new("RGB", (1024, 1024), (60, 60, 60))
    subject(ImageDraw.Draw(im))
    p = os.path.join(d, "dark_bg.jpg")
    im.save(p, "JPEG", quality=95)
    bad.append(p)

    return good, bad


def main():
    _out("ecom-white-batch 自检")
    _out("-" * 40)

    try:
        import numpy  # noqa: F401
        import cv2  # noqa: F401
        import PIL  # noqa: F401
        _out("[OK] 依赖齐全：pillow / opencv / numpy")
    except Exception as exc:
        _out("[FAIL] 缺依赖：%s" % exc)
        _out("       装一下：pip install -r requirements.txt")
        return 1

    work = tempfile.mkdtemp(prefix="ewb_selftest_")
    try:
        src = os.path.join(work, "in")
        dst = os.path.join(work, "out")
        os.makedirs(src)
        os.makedirs(dst)
        good, bad = _make_samples(src)
        _out("[OK] 合成测试图 %d 张（好 %d / 该被拒 %d）"
             % (len(good) + len(bad), len(good), len(bad)))

        cmd = [sys.executable, "-X", "utf8", os.path.join(HERE, "white_batch.py"),
               "--in", src, "--out", dst, "--canvas", "1024", "--jobs", "1"]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            _out("[FAIL] 白底链跑不通，退出码 %d" % proc.returncode)
            _out((proc.stderr or "")[-800:])
            return 1

        rp = os.path.join(dst, "_white_batch_report.json")
        if not os.path.exists(rp):
            _out("[FAIL] 没产出报告 _white_batch_report.json")
            return 1
        with open(rp, encoding="utf-8") as fh:
            rep = json.loads(fh.read())

        skipped = [r for r in rep["results"] if r.get("status") == "SKIP"]
        passed = [r for r in rep["results"] if r.get("status") == "PASS"]
        by_src = {os.path.basename(r["src"]): r for r in rep["results"]}

        bad_skipped = [b for b in bad
                       if by_src.get(os.path.basename(b), {}).get("status") == "SKIP"]
        if len(bad_skipped) != len(bad):
            _out("[FAIL] 入口闸门没拦住坏底色图：%d/%d 被拒"
                 % (len(bad_skipped), len(bad)))
            for r in skipped:
                _out("       %s -> %s" % (os.path.basename(r["src"]), r.get("why")))
            return 1
        _out("[OK] 入口闸门行为正确：坏底色 %d/%d 全部被拒"
             % (len(bad_skipped), len(bad)))

        if not passed:
            _out("[FAIL] 好底色图一张都没通过白底判定")
            for r in rep["results"]:
                _out("       %s -> %s %s" % (os.path.basename(r["src"]),
                                           r.get("status"), r.get("why") or ""))
            return 1
        _out("[OK] 白底链产出：候选 %d · 通过 %d" % (rep["candidates"], rep["pass"]))

        # 产出图的四角必须是纯白
        from PIL import Image
        import numpy as np
        for r in passed:
            p = r.get("dst")
            if not p or not os.path.exists(p):
                _out("[FAIL] 报告说有交付图但文件不存在：%s" % p)
                return 1
            a = np.asarray(Image.open(p).convert("RGB"))
            h, w = a.shape[:2]
            s = 24
            corners = [a[:s, :s], a[:s, w - s:], a[h - s:, :s], a[h - s:, w - s:]]
            cmin = int(min(int(c.reshape(-1, 3).min()) for c in corners))
            if cmin < 247:
                _out("[FAIL] 交付图四角不是纯白：%s（corner_min=%d）"
                     % (os.path.basename(p), cmin))
                return 1
        _out("[OK] 交付图四角纯白（corner_min >= 247）")

        _out("-" * 40)
        _out("环境可用 OK   链路口径：候选 %d · 通过 %d"
             % (rep["candidates"], rep["pass"]))
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
