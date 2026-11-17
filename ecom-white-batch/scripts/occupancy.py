# -*- coding: utf-8 -*-
"""occupancy.py — 主体占位与背景基色的度量尺子（自包含）。

来源：ecommerce-skills / platform-compliance/scripts/check_listing.py
      （由 scripts/build-skills.mjs 从 shared/scripts/check_listing.py 同步生成）
本文件只抽取其中 5 个纯度量函数，逐字保留其算法，不做任何改写，
目的是让本 skill 不依赖外部绝对路径即可运行。度量口径与开源机检保持一致。

  border_ring(img, frac)        取四边一圈像素
  bg_stats(img, tolerance)      背景基色 / 纯白比例 / 与基色一致比例
  subject_bbox(img, base)       主体包围盒 / 占位面积 / 墨量
  has_border(img, base)         最外一圈是否为异色细边
  ratio_ok(w, h, allowed, tol)  宽高比是否落在允许区间

只依赖 Pillow。
"""
from __future__ import annotations

from PIL import Image, ImageChops


def border_ring(img, frac=0.02):
    """取四边一圈像素，用来判定背景色。"""
    w, h = img.size
    d = max(1, int(min(w, h) * frac))
    px = img.load()
    out = []
    for y in list(range(d)) + list(range(h - d, h)):
        for x in range(0, w, max(1, w // 200)):
            out.append(px[x, y])
    for x in list(range(d)) + list(range(w - d, w)):
        for y in range(0, h, max(1, h // 200)):
            out.append(px[x, y])
    return out


def bg_stats(img, tolerance):
    """返回 (背景基色, 纯白比例, 与基色一致的比例)。"""
    ring = border_ring(img)
    if not ring:
        return (255, 255, 255), 0.0, 0.0
    white = sum(1 for p in ring if all(abs(c - 255) <= tolerance for c in p[:3]))
    # 众数近似：把颜色量化到 8 级再统计
    buckets = {}
    for p in ring:
        k = tuple(c // 32 for c in p[:3])
        buckets[k] = buckets.get(k, 0) + 1
    mode_k = max(buckets, key=buckets.get)
    members = [p[:3] for p in ring if tuple(c // 32 for c in p[:3]) == mode_k]
    base = tuple(round(sum(c[i] for c in members) / len(members)) for i in range(3))
    same = sum(1 for p in ring
               if sum((a - b) ** 2 for a, b in zip(p[:3], base)) ** 0.5 <= 24)
    return base, white / len(ring), same / len(ring)


def subject_bbox(img, base, thresh=18):
    """相对背景基色做差，得到主体包围盒与占位面积。"""
    w, h = img.size
    bg = Image.new("RGB", img.size, tuple(int(c) for c in base))
    diff = ImageChops.difference(img.convert("RGB"), bg).convert("L")
    mask = diff.point(lambda v: 255 if v > thresh else 0)
    box = mask.getbbox()
    if not box:
        return None, 0.0, 0.0
    ink = sum(mask.histogram()[1:]) / float(w * h)
    x0, y0, x1, y1 = box
    return box, ((x1 - x0) * (y1 - y0)) / float(w * h), ink


def has_border(img, base):
    """最外一圈是否是一条与背景不同的均匀细边。"""
    w, h = img.size
    if min(w, h) < 20:
        return False
    px = img.convert("RGB").load()
    ring = [px[x, 0] for x in range(0, w, max(1, w // 100))] + \
           [px[x, h - 1] for x in range(0, w, max(1, w // 100))]
    if not ring:
        return False
    far = sum(1 for p in ring
              if sum((a - b) ** 2 for a, b in zip(p, base)) ** 0.5 > 40)
    return far / len(ring) > 0.9


def ratio_ok(w, h, allowed, tol=0.02):
    if not allowed:
        return True, f"{w}:{h}"
    r = w / h
    for a, b in allowed:
        if abs(r - a / b) <= tol:
            return True, f"{a}:{b}"
    return False, f"{r:.3f}"
