# -*- coding: utf-8 -*-
"""擦掉白底图里的彩色污染 (G-WHITE-2 的修复动作)。

为什么需要
    环 4 的生图层由模型直出, 一定会带进它自己编的东西: 绿植、蓝色
    竖条、彩色装饰。G-WHITE-2 能量出这些是污染, 但闸门只会拦, 不会修。
    拦下来以后必须有一条能自动走完的修复动作, 闭环才成立。

为什么擦得掉
    彩色污染在白底图上永远是「贴」在背景上的, 背后的就是纯白底。
    所以把它的连通域整块填白是物理正确的, 不需要猜它原来是什么。

安全阀
    如果被擦面积超过画布 25%, 说明主体本身是彩色的 (彩色灯带/彩色灯罩),
    这条判据不适用, 直接报 NEEDS_HUMAN, 不硬擦。
"""
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image

SAT_MIN = 60
SAT_LO = 20
MIN_BLOB_PCT = 0.2
MAX_WIPE_PCT = 25.0
HOT_RATIO = 0.05


def purge(src, dst, sat_min=SAT_MIN, min_blob_pct=MIN_BLOB_PCT,
          max_wipe_pct=MAX_WIPE_PCT):
    a = np.asarray(Image.open(src).convert("RGB"))
    h, w = a.shape[:2]
    ai = a.astype(np.int16)
    mx = ai.max(axis=2)
    mn = ai.min(axis=2)
    sat = mx - mn
    nonwhite = mx < 245
    hot = (sat >= sat_min) & nonwhite
    cand = ((sat >= SAT_LO) & nonwhite).astype(np.uint8)
    if not cand.any():
        return {"file": None, "wiped_pct": 0.0, "blobs": 0,
                "status": "CLEAN", "src": str(src)}
    k5 = np.ones((5, 5), np.uint8)
    blob = cv2.dilate(cand, k5, iterations=2)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(blob, 8)
    neutral = ((sat < SAT_LO) & nonwhite).astype(np.uint8)
    nn, nlab, nstats, _ = cv2.connectedComponentsWithStats(neutral, 8)
    subject = np.zeros_like(neutral)
    if nn > 1:
        big = 1 + int(np.argmax(nstats[1:, cv2.CC_STAT_AREA]))
        subject = (nlab == big).astype(np.uint8)
        subject = cv2.dilate(subject, k5, iterations=2)
    keep = np.zeros_like(blob)
    min_px = min_blob_pct / 100.0 * w * h
    picked = 0
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < min_px:
            continue
        m = lab == i
        if not hot[m].any():
            continue
        if float(hot[m].sum()) / float(m.sum()) < HOT_RATIO:
            continue
        if (subject > 0).any() and (m & (subject > 0)).any():
            continue
        keep[m] = 1
        picked += 1
    if picked == 0:
        return {"file": None, "wiped_pct": 0.0, "blobs": 0,
                "status": "CLEAN", "src": str(src)}
    wipe_px = int(keep.sum())
    pct = 100.0 * wipe_px / float(w * h)
    if pct > max_wipe_pct:
        return {"file": None, "wiped_pct": round(pct, 3), "blobs": picked,
                "status": "NEEDS_HUMAN", "src": str(src),
                "why": "彩色像素占画布 %.2f%% > %.0f%%, 主体本身可能是彩色"
                       % (pct, max_wipe_pct)}
    out = a.copy()
    out[keep > 0] = 255
    Image.fromarray(out).save(dst, quality=95, subsampling=0)
    return {"file": str(dst), "wiped_pct": round(pct, 3), "blobs": picked,
            "status": "PURGED", "src": str(src)}


def main():
    if len(sys.argv) < 2:
        print("用法: python 241_purge_chroma.py <图 或 目录> [out_dir]")
        return
    tgt = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else None
    files = []
    if os.path.isdir(tgt):
        files = [os.path.join(tgt, n) for n in sorted(os.listdir(tgt))
                 if n.lower().endswith((".jpg", ".jpeg", ".png"))]
    else:
        files = [tgt]
    results = []
    for f in files:
        d = outdir or os.path.dirname(f)
        base = os.path.splitext(os.path.basename(f))[0]
        dst = os.path.join(d, base + "_purged.jpg")
        r = purge(f, dst)
        results.append(r)
        print("[%s] %s wiped=%.2f%% blobs=%d"
              % (base, r["status"], r["wiped_pct"], r["blobs"]))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
