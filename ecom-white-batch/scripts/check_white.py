# -*- coding: utf-8 -*-
"""白底合规硬检测 (不依赖模型, 直接量像素)。

为什么要单独做: 第三轮评估里"背景不合格"占 21/42, 但那是模型自述。
模型可能把轻微的 JPEG 压缩噪点或柔和阴影判成"非纯白"。
这里用程序量四角与四边的实际像素, 给出可复现的数字。

判定 (淘宝白底图规则 + 留 8 点容差):
  四角 RGB 全部 >= 247 视为合格纯白
  四角最大/最小通道差 <= 6 视为无色偏
  边缘若有明显暗像素(产品出血或阴影), 单独报出来

用法: python 06_check_white.py [目录, 默认 04_generated]
"""
import json
import os
import sys

import numpy as np
from PIL import Image

WHITE_MIN = 247      # 纯白下限, 留 8 点容差
NEUTRAL_SPREAD = 6   # 同一点三通道最大差, 超过算色偏


def check(path):
    try:
        im = Image.open(path).convert("RGB")
    except Exception as exc:
        return {"file": os.path.basename(path), "ok": False, "err": str(exc)}
    w, h = im.size
    # 四角各取 24x24 块, 取块内中位数抗压缩噪点
    s = 24
    corners = {
        "TL": (0, 0), "TR": (w - s, 0),
        "BL": (0, h - s), "BR": (w - s, h - s),
    }
    samples = {}
    for name, (x, y) in corners.items():
        box = im.crop((x, y, x + s, y + s))
        px = np.asarray(box).reshape(-1, 3)
        n = len(px)
        med = [int(sorted(px[:, i].tolist())[n // 2]) for i in range(3)]
        samples[name] = med
    vals = [v for med in samples.values() for v in med]
    corner_min = min(vals)
    spread = max(max(med) - min(med) for med in samples.values())

    # 边缘一圈: 有多少比例不是白, 用来抓产品出血/投影
    edge = []
    for crop in ((0, 0, w, 6), (0, h - 6, w, h), (0, 0, 6, h), (w - 6, 0, w, h)):
        edge.extend(np.asarray(im.crop(crop)).reshape(-1, 3).tolist())
    non_white = sum(1 for p in edge if min(p) < WHITE_MIN)

    return {
        "file": os.path.basename(path),
        "size": f"{w}x{h}",
        "corners": samples,
        "corner_min": corner_min,
        "pure_white": corner_min >= WHITE_MIN,
        "color_spread": spread,
        "neutral": spread <= NEUTRAL_SPREAD,
        "edge_non_white_pct": round(non_white / len(edge) * 100, 1),
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="白底合规硬检测：量四角与四边的实际像素，不依赖模型。")
    ap.add_argument("--dir", dest="d", default=".",
                    help="待检测目录，默认当前目录")
    ap.add_argument("--only", default=None, help="文件名只含该串才检测")
    ap.add_argument("--exclude", default=None, help="文件名含该串就跳过")
    ap.add_argument("--out", default=None, help="报告输出路径")
    args = ap.parse_args()
    d = os.path.abspath(args.d)
    files = sorted(f for f in os.listdir(d)
                   if f.lower().endswith((".jpg", ".jpeg", ".png")))
    if args.only:
        files = [f for f in files if args.only in f]
    if args.exclude:
        files = [f for f in files if args.exclude not in f]
    print("目录 %s, 共 %d 张" % (d, len(files)))
    recs = []
    for f in files:
        recs.append(check(os.path.join(d, f)))
    ok = [r for r in recs if r.get("pure_white")]
    lines = ["# 白底合规硬检测 (%s)" % os.path.basename(d), "",
             "纯白下限 RGB>=%d, 色偏阈值<=%d" % (WHITE_MIN, NEUTRAL_SPREAD), "",
             "| 文件 | 尺寸 | 四角最低通道 | 纯白 | 色偏 | 边缘非白占比 |",
             "|---|---|---|---|---|---|"]
    for r in recs:
        if "err" in r:
            lines.append("| %s | 读取失败: %s ||||" % (r["file"], r["err"]))
            continue
        lines.append("| %s | %s | %d | %s | %s | %.1f%% |" % (
            r["file"], r["size"], r["corner_min"],
            "是" if r["pure_white"] else "**否**",
            "是" if r["neutral"] else "**否**",
            r["edge_non_white_pct"]))
    lines += ["", "实测纯白 %d / %d" % (len(ok), len(recs)), ""]
    out_md = args.out or os.path.join(d, "_white_check.md")
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    out_json = os.path.splitext(out_md)[0] + ".json"
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(recs, fh, ensure_ascii=False, indent=2)
    print("实测纯白 %d / %d" % (len(ok), len(recs)))
    print("报告: %s" % out_md)


if __name__ == "__main__":
    main()
