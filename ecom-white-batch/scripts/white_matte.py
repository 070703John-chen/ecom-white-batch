# -*- coding: utf-8 -*-
"""White background post-process chain.

Ported from OneMix backend/onemix/services/dashscope_svc.py:
  - ensure_pure_white_background (paste on pure white canvas)
  - check_pure_white_background   (4 corners + >=95% white pixels)
  - enforce_file_size_jpg
  - postprocess_main_image

Fix applied: OneMix only pastes, it does not matte. MiniMax image-01 output
has a vignetted background (corner gray 196..250) so pasting keeps the
vignette. This module estimates a local background field from the border
ring, removes only background connected to the border, then composites.

Usage:
  python -X utf8 229_white_bg_chain.py <in_dir> <out_dir> [t_low] [t_high]
"""
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image

MAIN_W = 1440
MAIN_H = 1440


def estimate_bg_field(arr):
    """Estimate a smooth background colour field from the border ring."""
    h, w = arr.shape[:2]
    band = max(2, int(round(min(h, w) * 0.04)))
    rows = np.concatenate([arr[:, :band, :], arr[:, -band:, :]], axis=1)
    row_bg = np.median(rows, axis=1)
    cols = np.concatenate([arr[:band, :, :], arr[-band:, :, :]], axis=0)
    col_bg = np.median(cols, axis=0)
    return 0.5 * row_bg[:, None, :] + 0.5 * col_bg[None, :, :]


def estimate_bg_field_2d(arr, iters=4, grid=24, smooth_frac=0.10, tol=0.06):
    """Iterative robust 2D background field.

    The vignette is radial, so a row/column blend cannot follow it. Start from
    the row/column estimate, then repeatedly divide the image by the current
    field, take a masked median of the quotient on a coarse grid, smooth it,
    and fold it back into the field.
    """
    h, w = arr.shape[:2]
    full = arr.astype(np.float32)
    f = np.maximum(estimate_bg_field(arr).astype(np.float32), 1.0)
    cells = grid
    for _ in range(iters):
        ratio = full / np.maximum(f, 1.0)
        dev = np.abs(ratio - 1.0).max(axis=2)
        coarse = cv2.resize(ratio, (cells, cells), interpolation=cv2.INTER_AREA)
        cdev = cv2.resize(dev, (cells, cells), interpolation=cv2.INTER_AREA)
        med = np.zeros((cells, cells, 3), dtype=np.float32)
        for cy in range(cells):
            for cx in range(cells):
                y0 = cy * h // cells
                y1 = max(y0 + 1, (cy + 1) * h // cells)
                x0 = cx * w // cells
                x1 = max(x0 + 1, (cx + 1) * w // cells)
                m = dev[y0:y1, x0:x1] < tol
                if m.sum() >= 3:
                    med[cy, cx] = np.median(ratio[y0:y1, x0:x1][m], axis=0)
                else:
                    med[cy, cx] = 1.0
        for c in range(3):
            med[:, :, c] = cv2.GaussianBlur(med[:, :, c], (5, 5), 0)
        upd = cv2.resize(med, (w, h), interpolation=cv2.INTER_CUBIC)
        f = f * upd
    k = max(9, int(round(max(h, w) * smooth_frac)))
    k = k | 1
    for c in range(3):
        f[:, :, c] = cv2.GaussianBlur(f[:, :, c], (k, k), 0)
    return np.maximum(f, 1.0)


def matte_background(arr, t_low=14.0, t_high=42.0, work=512):
    """Return RGBA array with the border-connected background made transparent."""
    h, w = arr.shape[:2]
    sc = work / float(max(h, w))
    sw = max(8, int(round(w * sc)))
    sh = max(8, int(round(h * sc)))
    small = cv2.resize(arr, (sw, sh), interpolation=cv2.INTER_AREA)
    field = estimate_bg_field(small)
    d = np.abs(small.astype(np.float32) - field.astype(np.float32)).max(axis=2)
    cand = (d < t_low).astype(np.uint8)
    n, lab = cv2.connectedComponents(cand, connectivity=4)
    keep = np.zeros(n, dtype=bool)
    for l in range(1, n):
        m = lab == l
        if m[0, :].any() or m[-1, :].any() or m[:, 0].any() or m[:, -1].any():
            keep[l] = True
    fg = (~keep[lab]).astype(np.uint8)
    dist = cv2.distanceTransform(fg, cv2.DIST_L2, 3)
    alpha = np.clip(dist / 1.5, 0, 1)
    alpha = (alpha * 255).astype(np.uint8)
    alpha = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_LINEAR)
    alpha = cv2.GaussianBlur(alpha, (0, 0), 1.2)
    return np.dstack([arr, alpha])


def flatfield_matte(arr, tol=0.022, work=512, feather=1.2):
    """Flat-field normalised matte.

    The MiniMax vignette is multiplicative, so absolute distance overstates
    the difference on bright pixels. Normalise by the estimated background
    field first, then threshold on relative deviation. This keeps a white
    frosted product separable from a light grey background.
    Background pixels are replaced by pure white instead of alpha 0, which
    avoids a halo on the subject edge.
    """
    h, w = arr.shape[:2]
    sc = work / float(max(h, w))
    sw = max(8, int(round(w * sc)))
    sh = max(8, int(round(h * sc)))
    small = cv2.resize(arr, (sw, sh), interpolation=cv2.INTER_AREA)
    field = estimate_bg_field_2d(small)
    ratio = small.astype(np.float32) / field
    dev = np.abs(ratio - 1.0).max(axis=2)
    cand = (dev < tol).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab = cv2.connectedComponents(cand, connectivity=4)
    keep = np.zeros(n, dtype=bool)
    for l in range(1, n):
        m = lab == l
        if m[0, :].any() or m[-1, :].any() or m[:, 0].any() or m[:, -1].any():
            keep[l] = True
    bg = keep[lab]
    fg = (~bg).astype(np.uint8)
    dist = cv2.distanceTransform(fg, cv2.DIST_L2, 3)
    soft = np.clip(dist / 2.0, 0, 1)
    soft = cv2.resize(soft, (w, h), interpolation=cv2.INTER_LINEAR)
    soft = cv2.GaussianBlur(soft, (0, 0), max(0.6, feather))
    out = arr.astype(np.float32) * soft[:, :, None] + 255.0 * (1.0 - soft[:, :, None])
    return np.clip(out, 0, 255).astype(np.uint8)


def composite_white(rgba, dest, width=MAIN_W, height=MAIN_H):
    """Paste the RGBA subject on a pure white canvas, OneMix style."""
    dest = __import__("pathlib").Path(dest)
    im = Image.fromarray(rgba, "RGBA")
    # 放大策略出处：Real-ESRGAN（xinntao/Real-ESRGAN）是我们记档的标准做法，
    #   但本体要 GPU + 另下权重，CPU 上跑一张要几十秒到几分钟。
    #   本环节只做「缩到画布尺寸 / 贴纯白底」，不是最终交付大图，
    #   所以用 PIL LANCZOS 等价替代，不为了「接上」而装一个跑不动的包。
    #   真要做最终大图超分，另起一环专门跑 Real-ESRGAN，不要塞进这一环。
    im.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    canvas.paste(im, ((width - im.width) // 2, (height - im.height) // 2), im)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "JPEG", quality=92, optimize=True)


def check_pure_white(path, sample_step=5):
    """OneMix rule: 4 corners exactly (255,255,255) and >=95% pure white pixels."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    for x, y in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if px[x, y] != (255, 255, 255):
            return False
    total = 0
    white = 0
    for y in range(0, h, max(1, sample_step)):
        for x in range(0, w, max(1, sample_step)):
            total += 1
            if px[x, y] == (255, 255, 255):
                white += 1
    return total > 0 and (white / total) >= 0.95


def enforce_file_size_jpg(path, min_kb=0, max_kb=1024):
    """OneMix file size clamp by JPEG quality bisection."""
    import io
    import pathlib

    path = pathlib.Path(path)
    data = path.read_bytes()
    size_kb = len(data) / 1024
    if min_kb <= size_kb <= max_kb:
        return
    im = Image.open(path).convert("RGB")
    low, high = 30, 95
    best = None
    for _ in range(12):
        q = (low + high) // 2
        bio = io.BytesIO()
        im.save(bio, "JPEG", quality=q, optimize=True)
        b = bio.getvalue()
        kb = len(b) / 1024
        if kb > max_kb:
            high = q - 1
        elif kb < min_kb:
            low = q + 1
            best = b
        else:
            best = b
            break
    if best is None:
        bio = io.BytesIO()
        im.save(bio, "JPEG", quality=max(30, min(95, high)), optimize=True)
        best = bio.getvalue()
    path.write_bytes(best)


def metrics(path):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
    cg = [int(round(sum(c) / 3.0)) for c in corners]
    total = 0
    w250 = 0
    w255 = 0
    dark = 0
    mid = 0
    for y in range(0, h, 5):
        for x in range(0, w, 5):
            total += 1
            p = px[x, y]
            if min(p) >= 250:
                w250 += 1
            if p == (255, 255, 255):
                w255 += 1
            if min(p) < 200:
                dark += 1
            if min(p) < 235:
                mid += 1
    return {
        "size": "%dx%d" % (w, h),
        "kb": round(os.path.getsize(path) / 1024.0, 1),
        "corner_rgb": [list(c) for c in corners],
        "corner_gray": cg,
        "corner_gray_min": min(cg),
        "corner_gray_max": max(cg),
        "ratio_ge250": round(w250 / float(total), 4),
        "ratio_eq255": round(w255 / float(total), 4),
        "dark_ratio": round(dark / float(total), 4),
        "mid_ratio": round(mid / float(total), 4),
    }


def main():
    in_dir = sys.argv[1]
    out_dir = sys.argv[2]
    mode = sys.argv[3] if len(sys.argv) > 3 else "flat"
    p1 = float(sys.argv[4]) if len(sys.argv) > 4 else 0.022
    p2 = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
    os.makedirs(out_dir, exist_ok=True)
    names = sorted(n for n in os.listdir(in_dir) if n.lower().endswith((".jpg", ".jpeg", ".png")))
    report = []
    for name in names:
        src = os.path.join(in_dir, name)
        dst = os.path.join(out_dir, name)
        arr = np.asarray(Image.open(src).convert("RGB"))
        before = metrics(src)
        if mode == "flat":
            flat = flatfield_matte(arr, tol=p1)
            Image.fromarray(flat).save(dst, "JPEG", quality=92, optimize=True)
        else:
            rgba = matte_background(arr, t_low=p1, t_high=(p2 or p1 * 3))
            composite_white(rgba, dst)
        enforce_file_size_jpg(dst, min_kb=38, max_kb=300)
        after = metrics(dst)
        after["onemix_pass"] = check_pure_white(dst)
        after["dark_keep"] = round(
            after["dark_ratio"] / max(before["dark_ratio"], 1e-6), 3)
        report.append({
            "name": name,
            "mode": mode,
            "p1": p1,
            "p2": p2,
            "before": before,
            "after": after,
        })
        print("[%s] before cg=%s r255=%.3f dark=%.4f | after cg=%s r255=%.3f dark=%.4f keep=%.2f pass=%s"
              % (name, before["corner_gray"], before["ratio_eq255"],
                 before["dark_ratio"],
                 after["corner_gray"], after["ratio_eq255"], after["dark_ratio"],
                 after["dark_keep"], after["onemix_pass"]), flush=True)
    out_json = os.path.join(out_dir, "_white_bg_report.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    n = len(report)
    ok = sum(1 for r in report if r["after"]["onemix_pass"])
    print("[done] %d images, OneMix white pass %d/%d" % (n, ok, n))
    print("[report] %s" % out_json)


if __name__ == "__main__":
    main()
