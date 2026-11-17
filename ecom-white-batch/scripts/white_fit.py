# -*- coding: utf-8 -*-
"""主体占画布适配（白底链第 4 步）

为什么要有这一步
    抠底那步的 composite_white() 里是 im.thumbnail((1440, 1440)) —— 只缩不放。
    生图路线出图是 1024x1024，主体像素原样贴到 1440x1440 画布上，
    包围盒占比从 0.2236 掉到 0.1124，开源上架机检直接判「商品太小」驳回。
    真实图路线本来就有「裁 bbox + 放大 + margin」，两条路线口径不一致，
    这里把生图路线补齐成同一口径。

口径（不自造）
    占比一律用电商上架机检的开源口径（subject_bbox() / bg_stats()），
    该算法已逐字内联在同目录的 occupancy.py 里（不依赖任何外部路径）。
    目标占比 = 竞品原图同一口径的占比，clamp 到 [0.35, 0.80]。

画质约束
    放大倍数封顶 max_upscale（默认 2.0），放大过头宁可占比不够也不给糊图。
    实际占比、放大倍数、裁切框全部回写进批处理报告，可查可追。
"""
from __future__ import annotations

import importlib.util
import os

import cv2
import numpy as np
from PIL import Image

_checker = None


def _os_checker():
    """加载随包附带的度量尺子 occupancy.py（与开源机检同一口径，逐字搬运）。"""
    global _checker
    if _checker is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "occupancy.py")
        spec = importlib.util.spec_from_file_location(
            "occupancy", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _checker = mod
    return _checker


def occupancy_of(path, tolerance=6):
    """用开源 subject_bbox 口径算一张图的包围盒占比。"""
    mod = _os_checker()
    img = Image.open(path).convert("RGB")
    base, _white, _same = mod.bg_stats(img, tolerance)
    _box, occ, _ink = mod.subject_bbox(img, base)
    return round(float(occ), 4)


def occupancy_of_array(rgb, tolerance=6):
    """同上，但直接量一张内存里的 RGB 数组（给闭环迭代用）。"""
    mod = _os_checker()
    img = Image.fromarray(rgb, "RGB")
    base, _white, _same = mod.bg_stats(img, tolerance)
    _box, occ, _ink = mod.subject_bbox(img, base)
    return float(occ)


def bbox_of(path, tolerance=6):
    """用开源口径取包围盒 (x0, y0, x1, y1) 和占比。"""
    mod = _os_checker()
    img = Image.open(path).convert("RGB")
    base, _white, _same = mod.bg_stats(img, tolerance)
    box, occ, _ink = mod.subject_bbox(img, base)
    if not box:
        return None, 0.0
    return box, round(float(occ), 4)


def longside_ratio(path, tolerance=6):
    """主体最长边 / 画布边 —— 与主体形状无关的「在画面里有多大」。"""
    box, _occ = bbox_of(path, tolerance)
    if not box:
        return 0.0
    w, h = Image.open(path).size
    bw = box[2] - box[0]
    bh = box[3] - box[1]
    return round(float(max(bw, bh)) / float(max(w, h)), 4)


def subject_bbox_from_alpha(rgba, thr=8):
    """alpha 前景的包围盒（含羽化边）。"""
    a = rgba[:, :, 3]
    ys, xs = np.nonzero(a > thr)
    if ys.size == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def _subject_mask(rgba, thr=8, white_thr=246):
    """主体掩膜。

    真实图路线有两种底子:
      alpha_pixels —— alpha 有真值, 直接用 alpha
      crop         —— alpha 全 255, 只能按「不是纯白」找主体
    所以 alpha 覆盖接近整幅时自动降级到非白判定, 否则整张图都会被当成主体。
    """
    a = rgba[:, :, 3]
    cov = float((a > thr).mean())
    if cov < 0.95:
        return a > thr, "alpha"
    rgb = rgba[:, :, :3].astype(np.int16)
    nonwhite = rgb.min(axis=2) < white_thr
    if not nonwhite.any():
        return a > thr, "alpha"
    return nonwhite, "nonwhite"

def fit_subject(rgba, ref_occ=None, canvas=1024, margin=0.06,
                occ_min=0.35, occ_max=0.80, max_upscale=2.0,
                iters=4, ref_longside=None):
    """裁主体 bbox + 按目标占比放大 + 贴纯白画布。

    返回 (rgb_uint8, info dict)。
    """
    h, w = rgba.shape[:2]
    info = {
        "source_size": [w, h],
        "canvas": canvas,
        "margin": margin,
        "ref_occ": ref_occ,
        "occ_before": None,
        "occ_target": None,
        "occ_after": None,
        "scale": None,
        "bbox_in": None,
        "bbox_out": None,
        "capped": None,
        "occ_ceiling": None,
        "longside_ratio": None,
    }
    mask, mask_mode = _subject_mask(rgba)
    info["mask_mode"] = mask_mode
    ys, xs = np.nonzero(mask)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1,
            int(ys.max()) + 1) if ys.size else None
    if bbox is None:
        out = np.full((canvas, canvas, 3), 255, dtype=np.uint8)
        info["error"] = "no_subject"
        return out, info
    x0, y0, x1, y1 = bbox
    info["bbox_in"] = [x0, y0, x1, y1]
    sub = rgba[y0:y1, x0:x1]
    sh, sw = sub.shape[:2]
    # 先把主体按 alpha 压到白底（羽化边不产生灰晕），再缩放
    if mask_mode == "alpha":
        al = sub[:, :, 3:4].astype(np.float32) / 255.0
    else:
        al = mask[y0:y1, x0:x1][:, :, None].astype(np.float32)
    flat = np.clip(sub[:, :, :3].astype(np.float32) * al
                   + 255.0 * (1 - al), 0, 255).astype(np.uint8)
    # 原图口径的占比（未适配前）
    info["occ_before"] = round(float(sw * sh) / float(w * h), 4)
    target = 0.60 if ref_occ is None else float(ref_occ)
    target = max(occ_min, min(occ_max, target))
    info["occ_target"] = round(target, 4)
    # 目标尺度 = 竞品原图的主体占画布比例 (最长边口径)。
    # 为什么用最长边而不是包围盒面积:
    #   包围盒面积对形状敏感 —— 5 条斜铺的灯带 bbox 面积 0.5586,
    #   一根竖直的线圈 bbox 面积只有 0.44 但其实一样「占满画面」。
    #   平台那套 0.70 的面积阈值对细长主体本来就不公平 (竞品自己也不达标),
    #   复刻任务的标准答案只能是原图的**取景**, 所以跟原图的最长边占比对齐。
    scale_area = float(np.sqrt(target * canvas * canvas / float(sw * sh)))
    if ref_longside:
        want_long = max(0.30, min(1.0 - 2.0 * margin, float(ref_longside)))
        scale_long = want_long * canvas / float(max(sw, sh))
        info["longside_target"] = round(want_long, 4)
    else:
        want_long = None
        scale_long = None
        info["longside_target"] = None
    # R100r: 起点取「占比目标」与「竞品取景」里**更大**的那个,
    # 但收敛只由占比目标驱动。
    # 实测 S18 白底: 生成图主体已经占 49% 画幅, 旧逻辑为对齐竞品
    # longest-side(0.5953) 把主体缩到 59.5%, 结果三平台「主体占比」全挂
    # (occupancy_ratio 0.418, longside_ratio 0.843 < 0.9)。
    # 竞品取景是**上限参考**, 不是把主体缩小的理由。
    scale = max(scale_area, scale_long) if scale_long else scale_area
    info["scale_init"] = round(scale, 4)
    info["drive"] = "occupancy"
    # 留白约束：主体最长边不能顶到画布边（230 白底闸门 + 上架机检都要留白）
    s_max = canvas * (1.0 - 2.0 * margin) / float(max(sw, sh))
    s_cap = min(s_max, max_upscale)
    # 上限那一档能到的最高占比（主体形状决定，不是缩放能改的）
    info["occ_ceiling"] = round(
        float(sw * s_cap * sh * s_cap) / float(canvas * canvas), 4)
    if scale > s_cap + 1e-6:
        info["capped"] = {"want": round(scale, 4), "cap": round(s_cap, 4),
                          "reason": "margin_or_max_upscale",
                          "note": "主体形状太扁/太长，放到画布上限也只能到 "
                                  "occ_ceiling，这是构图差异不是缩放能修的"}
    scale = min(scale, s_cap)
    info["scale"] = round(scale, 4)
    def render(sc):
        nw = max(1, int(round(sw * sc)))
        nh = max(1, int(round(sh * sc)))
        prod = cv2.resize(flat, (nw, nh), interpolation=cv2.INTER_LANCZOS4)
        c = np.full((canvas, canvas, 3), 255, dtype=np.uint8)
        ox = (canvas - nw) // 2
        oy = (canvas - nh) // 2
        c[oy:oy + nh, ox:ox + nw] = prod
        return c, nw, nh, ox, oy

    # 闭环: alpha bbox 不等于「非白像素」bbox, 一次算不准。
    # 每轮按开源口径实测的占比反推缩放系数, 迭代到收敛。
    out = None
    nw = nh = ox = oy = 0
    meas = None
    used_scale = scale
    for it in range(max(1, iters)):
        used_scale = scale
        out, nw, nh, ox, oy = render(scale)
        meas = occupancy_of_array(out)
        meas_long = float(max(nw, nh)) / float(canvas)
        if meas <= 0:
            break
        # R100r: 闭环只由**面积占比目标**驱动。
        # 旧逻辑在有 ref_longside 时改按长边收敛, 于是把主体缩到竞品取景,
        # 占比掉到平台下限以下 (S18 白底 occ_after 0.1446, 三平台全挂)。
        # 竞品长边只作为**起始尺度下限**用 (scale = max(area, long)),
        # 收敛阶段不再看它。
        if abs(meas - target) <= 0.01:
            break
        scale = scale * float(np.sqrt(target / meas))
        scale = min(scale, s_cap)
    info["bbox_out"] = [ox, oy, ox + nw, oy + nh]
    info["occ_after"] = round(float(meas) if meas is not None else 0.0, 4)
    info["occ_after_alpha_bbox"] = round(float(nw * nh) / float(canvas * canvas), 4)
    # 占高/占宽: 与包围盒面积无关的「主体在画面里有多大」。
    # 包围盒面积对细长主体不公平 (一条横贯画面的细线 bbox 占满宽但面积很小),
    # 所以两个数都记, 判断时看哪个适用。
    info["longside_ratio"] = round(
        float(max(nw, nh)) / float(canvas), 4)
    info["iterations"] = it + 1
    info["scale"] = round(float(used_scale), 4)
    return out, info
