# -*- coding: utf-8 -*-
"""批量白底一条命令跑批 (R99zp)。

为什么要有它
    白底这条链的零件早就齐了: 229 抠底 + 241 去彩污 + 244 主体占位
    + 06 纯白检测。但零件是零件, 每次手工按顺序敲四遍, 就永远只是
    "偶尔做出一张", 不是"能批量复制"。
    这个脚本把四步接成一条命令, 输入一批源图, 输出 1:1 纯白交付图
    + 一份 n/N 报告。

口径 (不造新标准, 全部沿用已有零件)
    抠底        229.matte_background (边框连通背景抠掉)
    去彩污      241.purge (G-WHITE-2 的修复动作, 可选)
    主体占位    244.fit_subject (开源 check_listing.py 同一把尺子, 闭环迭代)
    纯白判定    06_check_white 同一把尺子 (四角中位数 >=247, 色偏 <=6)

3:4 -> 1:1 怎么解决
    image-01 只收 3:4, 淘宝白底主图要 1:1。244 是按"主体 bbox 裁出来
    再贴方形画布"做的, 所以 3:4 进来自动按主体取景变 1:1, 不靠硬裁。

用法
    python -X utf8 white_batch.py --in 04_generated --out 04_white_batch
    python -X utf8 white_batch.py --in A/ --out B/ --purge --canvas 1200
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image
import cv2

try:  # 终端编码可能是 GBK / cp936，别让中文输出把脚本打断
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PIPE = os.path.dirname(os.path.abspath(__file__))
WHITE_MIN = 247      # 与 06_check_white.py 同
NEUTRAL_SPREAD = 6   # 与 06_check_white.py 同


def _load(name):
    path = os.path.join(PIPE, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


matte = _load("white_matte")
purge_mod = _load("white_purge")
fit = _load("white_fit")


def corner_stats(rgb):
    """四角各 24x24 块取中位数, 和 06_check_white.py 完全同一把尺子。"""
    h, w = rgb.shape[:2]
    s = 24
    meds = []
    for (x, y) in ((0, 0), (w - s, 0), (0, h - s), (w - s, h - s)):
        blk = rgb[y:y + s, x:x + s].reshape(-1, 3)
        meds.append([int(np.median(blk[:, c])) for c in range(3)])
    vals = [v for m in meds for v in m]
    spread = max(max(m) - min(m) for m in meds)
    return {"corners": meds, "corner_min": min(vals),
            "spread": spread,
            "pure_white": min(vals) >= WHITE_MIN,
            "neutral": spread <= NEUTRAL_SPREAD}


def bg_purity(rgb):
    """背景干净度 —— 只量四角是不够的 (R99zp 第一轮实测打脸)。

    第一轮跑 49 张, 机器按「四角纯白」判 48/49 PASS, 人眼看就抓到
    G02 主图: 整块淡紫背景没抠掉, 四角照样是白的, 机检照样放行。
    所以这里加两道:
      非白像素占比 —— 主体 + 残留背景
      最大连通域外框占比 —— 背景没抠干净时, 会连成一大块
    """
    g = rgb.min(axis=2)
    nw = (g < 245).astype(np.uint8)
    ratio = float(nw.mean())
    n, lab, stats, _ = cv2.connectedComponentsWithStats(nw, 8)
    if n <= 1:
        return {"nonwhite_ratio": round(ratio, 4), "blob_ratio": 0.0,
                "blob_bbox_ratio": 0.0, "blobs": 0}
    areas = stats[1:, cv2.CC_STAT_AREA]
    big = 1 + int(np.argmax(areas))
    bb = stats[big]
    bbox_area = int(bb[cv2.CC_STAT_WIDTH]) * int(bb[cv2.CC_STAT_HEIGHT])
    canvas = rgb.shape[0] * rgb.shape[1]
    return {
        "nonwhite_ratio": round(ratio, 4),
        "blob_ratio": round(float(areas.max()) / float(max(int(nw.sum()), 1)), 4),
        "blob_bbox_ratio": round(bbox_area / float(canvas), 4),
        "blobs": int(n - 1),
    }


def source_ring(path):
    """源图边缘环的底色 —— 判定「这张图该不该走白底路线」。

    R99zp 第二轮实测: 白底链只对「浅色中性底」的图成立。
      G01 主图  ring sat=4  min=244 -> 抠得干净 (人眼确认可用)
      G02 主图  ring sat=44 min=153 -> 淡紫底抠不掉, 机检照样放行
      附图细节  ring sat=40~89 min<=5 -> 场景图, 本来就不该走白底
    所以入口必须先挑片, 不然就是「把场景图硬抠成白底」的假活。
    """
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.int16)
    h, w = a.shape[:2]
    b = max(2, int(min(h, w) * 0.04))
    ring = np.concatenate([a[:b].reshape(-1, 3), a[-b:].reshape(-1, 3),
                           a[:, :b].reshape(-1, 3), a[:, -b:].reshape(-1, 3)])
    med = np.median(ring, axis=0)
    sat = int(med.max() - med.min())
    return {"ring_rgb": [int(v) for v in med], "ring_sat": sat,
            "ring_min": int(med.min())}


def find_ref_longside(src_path, ref_dir):
    """参考图同名文件的最长边占比, 用来把取景对齐到竞品。"""
    if not ref_dir:
        return None
    base = os.path.splitext(os.path.basename(src_path))[0]
    for ext in (".jpg", ".jpeg", ".png", ".JPG", ".PNG"):
        p = os.path.join(ref_dir, base + ext)
        if os.path.isfile(p):
            try:
                return fit.longside_ratio(p)
            except Exception:
                return None
    return None


def process_one(src, dst, args):
    rec = {"src": src, "dst": dst}
    try:
        ring = source_ring(src)
        rec["source_ring"] = ring
        if not args.no_src_gate:
            if ring["ring_sat"] > args.max_ring_sat:
                rec["status"] = "SKIP"
                rec["why"] = ("源图底色有色 (边缘环饱和度 %d > %d), 不是白底候选"
                              % (ring["ring_sat"], args.max_ring_sat))
                return rec
            if ring["ring_min"] < args.min_ring_min:
                rec["status"] = "SKIP"
                rec["why"] = ("源图底色偏暗 (边缘环最暗通道 %d < %d), 不是白底候选"
                              % (ring["ring_min"], args.min_ring_min))
                return rec
        work = src
        tmpdir = None
        if args.purge:
            tmpdir = tempfile.mkdtemp(prefix="wbp_")
            work = os.path.join(tmpdir, os.path.basename(src))
            pr = purge_mod.purge(src, work)
            rec["purge"] = pr.get("status")
            if pr.get("status") == "NEEDS_HUMAN":
                rec["status"] = "NEEDS_HUMAN"
                rec["why"] = "彩色面积过大, 主体本身可能是彩色, 不硬擦"
                return rec
            if pr.get("file") is None:
                work = src

        arr = np.asarray(Image.open(work).convert("RGB"))
        rec["source_size"] = [int(arr.shape[1]), int(arr.shape[0])]

        rgba = matte.matte_background(arr, t_low=args.t_low)
        cov = float((rgba[:, :, 3] > 8).mean())
        if cov > 0.97:
            # 整张都被当成前景, alpha 不可用 -> 换平场法(把背景直接刷白)
            flat = matte.flatfield_matte(arr, tol=0.022)
            rgba = np.dstack([flat, np.full(flat.shape[:2], 255, np.uint8)])
            rec["matte_mode"] = "flat_fallback"
        else:
            rec["matte_mode"] = "alpha"
        rec["alpha_cov"] = round(cov, 4)

        ref_ls = find_ref_longside(src, args.ref_dir)
        if ref_ls:
            rec["ref_longside"] = ref_ls

        out, info = fit.fit_subject(
            rgba, ref_occ=args.occ, canvas=args.canvas,
            margin=args.margin, max_upscale=args.max_upscale,
            iters=args.iters, ref_longside=ref_ls)
        rec["fit"] = info

        st = corner_stats(out)
        rec["white"] = st
        rec["occupancy"] = info.get("occ_after")
        bp = bg_purity(out)
        rec["bg"] = bp

        Image.fromarray(out).save(dst, "JPEG", quality=94, optimize=True)

        if "error" in info:
            rec["status"] = "FAIL"
        elif bp["blob_bbox_ratio"] > args.max_bg_bbox:
            rec["status"] = "FAIL"
            rec["why"] = ("背景没抠干净: 最大非白连通域外框占画布 %.1f%% > %.0f%%"
                          % (bp["blob_bbox_ratio"] * 100,
                             args.max_bg_bbox * 100))
        elif bp["blob_ratio"] < args.min_blob_ratio:
            rec["status"] = "FAIL"
            rec["why"] = ("非白像素散在多处: 最大连通域只占非白像素 %.1f%% < %.0f%%"
                          % (bp["blob_ratio"] * 100,
                             args.min_blob_ratio * 100))
        elif st["pure_white"] and st["neutral"]:
            rec["status"] = "PASS"
        else:
            rec["status"] = "FAIL"
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return rec
    except Exception as exc:
        rec["status"] = "ERROR"
        rec["err"] = "%s: %s" % (type(exc).__name__, exc)
        rec["trace"] = traceback.format_exc()[-800:]
        return rec


def collect(src_root, pattern):
    files = []
    for dirpath, _dirs, names in os.walk(src_root):
        for n in sorted(names):
            if n.lower().endswith((".jpg", ".jpeg", ".png")) and pattern(n):
                files.append(os.path.join(dirpath, n))
    return sorted(files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--canvas", type=int, default=1200)
    ap.add_argument("--occ", type=float, default=0.60)
    ap.add_argument("--margin", type=float, default=0.06)
    ap.add_argument("--max-upscale", type=float, default=2.0)
    ap.add_argument("--iters", type=int, default=4)
    ap.add_argument("--t-low", dest="t_low", type=float, default=14.0)
    ap.add_argument("--purge", action="store_true")
    ap.add_argument("--ref-dir", dest="ref_dir", default=None)
    ap.add_argument("--suffix", default="_white")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--only", default=None, help="文件名只含这个串才处理")
    ap.add_argument("--exclude", default=None, help="文件名含这个串就跳过")
    ap.add_argument("--max-bg-bbox", dest="max_bg_bbox", type=float, default=0.85,
                    help="最大非白连通域外框占画布上限, 超过判背景没抠干净")
    ap.add_argument("--min-blob-ratio", dest="min_blob_ratio", type=float, default=0.70,
                    help="最大连通域占全部非白像素的最小比例, 低于判残留散块")
    ap.add_argument("--max-ring-sat", dest="max_ring_sat", type=int, default=12,
                    help="源图边缘环饱和度上限, 超过判不是白底候选")
    ap.add_argument("--min-ring-min", dest="min_ring_min", type=int, default=200,
                    help="源图边缘环最暗通道下限, 低于判不是白底候选")
    ap.add_argument("--no-src-gate", dest="no_src_gate", action="store_true",
                    help="关掉入口挑片闸门(调试用, 不建议)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    src_root = os.path.abspath(args.src)
    dst_root = os.path.abspath(args.dst)
    os.makedirs(dst_root, exist_ok=True)

    def keep(n):
        if args.only and args.only not in n:
            return False
        if args.exclude and args.exclude in n:
            return False
        return True

    files = collect(src_root, keep)
    if args.limit:
        files = files[:args.limit]
    print("待处理 %d 张 (机器并发 %d)" % (len(files), args.jobs), flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futs = []
        for f in files:
            rel = os.path.relpath(f, src_root)
            base, _ext = os.path.splitext(rel)
            dst = os.path.join(dst_root, base + args.suffix + ".jpg")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            futs.append(pool.submit(process_one, f, dst, args))
        for fu in futs:
            rec = fu.result()
            results.append(rec)
            print("[%s] %s occ=%s corner=%s" % (
                os.path.basename(rec.get("src", "?")),
                rec.get("status"),
                rec.get("occupancy"),
                (rec.get("white") or {}).get("corner_min")), flush=True)

    ok = sum(1 for r in results if r.get("status") == "PASS")
    skip = sum(1 for r in results if r.get("status") == "SKIP")
    cand = len(results) - skip
    report = {"total": len(results), "candidates": cand, "skipped": skip,
              "pass": ok,
              "pass_rate": round(ok / len(results), 4) if results else 0.0,
              "candidate_pass_rate": round(ok / cand, 4) if cand else 0.0,
              "canvas": args.canvas, "occ_target": args.occ,
              "results": results}
    rp = os.path.join(dst_root, "_white_batch_report.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("\n白底候选 %d 张 (入口挑片跳过 %d 张) · 纯白通过 %d / %d"
          % (cand, skip, ok, cand))
    print("报告 %s" % rp)


if __name__ == "__main__":
    main()
