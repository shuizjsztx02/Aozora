#!/usr/bin/env python3
"""T13-1 (REQ-45 决策#4): 四主题背景图清洗 + WebP 管线.

输入:  .scratch/t13/src/{22,23,24,25}.png  (1123x1999 豆包水彩图)
       22->dawn 23->day 24->dusk 25->night
输出:  app/public/theme/<name>.webp  (quality 82, 尺寸不动, 单图<=300KB)
辅助:  --check-dir DIR  把每个清洗区 4x 放大存 DIR 供交付目检

瑕疵处理:
  - 手动矩形: 翻页箭头(23/25 左右缘中部) / 「豆包AI生成」水印(四图右下)
    / 电池药丸(24 右上), 逐块 Poisson 线性扩散填充 (cv2.solvePoisson)
  - 自动检测: 24 四角白色圆角遮罩 —— 边缘带内近纯白连通域, 坐标以本脚本
    输出为准 (决策#4)

复跑前提: .scratch/t13/src/ 源图在工作树内 (不入库, 见 T13-1 log).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SRC_DIR = REPO / ".scratch" / "t13" / "src"
OUT_DIR = REPO / "app" / "public" / "theme"

# 源图: 编号 -> (主题名, [手动瑕疵矩形 (x0,y0,x1,y1)])
WM = (866, 1901, 1123, 1994)  # 「豆包AI生成」水印, 四图同位
IMAGES: dict[str, tuple[str, list[tuple[int, int, int, int]]]] = {
    "22": ("dawn", [WM]),
    "23": ("day", [(14, 934, 122, 1044), (1048, 934, 1123, 1046), WM]),
    "24": ("dusk", [(971, 29, 1072, 97), WM]),   # 电池药丸；四角白圆角自动检测
    "25": ("night", [(14, 941, 122, 1052), (1034, 931, 1123, 1049), WM]),
}

INPAINT_RADIUS = 7
MAX_KB = 300
WHITE_THRESH = 249       # 圆角遮罩近纯白
CORNER_WINDOW = 170      # 只在四角窗口内检测白遮罩
MASK_DILATE = 6          # 瑕疵 mask 外扩，吃抗锯齿边缘


def detect_white_corners(img: np.ndarray) -> list[tuple[int, int, int, int]]:
    """24 四角白色圆角遮罩: 边缘带内近纯白、贴边连通域 -> 外接矩形."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    boxes: list[tuple[int, int, int, int]] = []
    for cy, cx in ((0, 0), (0, w), (h, 0), (h, w)):  # 四角
        y0, y1 = sorted((max(cy - CORNER_WINDOW, 0), min(cy + CORNER_WINDOW, h)))
        x0, x1 = sorted((max(cx - CORNER_WINDOW, 0), min(cx + CORNER_WINDOW, w)))
        win = gray[y0:y1, x0:x1]
        mask = (win >= WHITE_THRESH).astype(np.uint8) * 255
        n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        for i in range(1, n):
            bx, by, bw, bh, area = stats[i]
            # 必须贴窗口角边（贴图像外缘），排除画作内白色高光
            touches = (
                (cy == 0 and by == 0) or (cy == h and by + bh == mask.shape[0])
            ) and ((cx == 0 and bx == 0) or (cx == w and bx + bw == mask.shape[1]))
            if touches and area >= 400:
                boxes.append((x0 + bx, y0 + by, x0 + bx + bw, y0 + by + bh))
    return boxes


def build_mask(shape: tuple[int, ...], boxes: list[tuple[int, int, int, int]]):
    h, w = shape[:2]
    mask = np.zeros((h, w), np.uint8)
    for x0, y0, x1, y1 in boxes:
        mask[max(y0, 0):min(y1, h), max(x0, 0):min(x1, w)] = 255
    if mask.any():
        mask = cv2.dilate(
            mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MASK_DILATE * 2 + 1,) * 2)
        )
    return mask


def _harmonic_fill_channel(chan: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """调和延拓精确解: 域内 Δf=0 (五点差分), 域外像素为 Dirichlet 边界."""
    from scipy import sparse
    from scipy.sparse.linalg import spsolve

    idx = np.full(chan.shape, -1, np.int64)
    ys, xs = np.nonzero(mask)
    n = len(ys)
    if n == 0:
        return chan
    idx[ys, xs] = np.arange(n)
    rows, cols, vals = [], [], []
    b = np.zeros(n, np.float64)
    for k in range(n):
        y, x = ys[k], xs[k]
        rows.append(k); cols.append(k); vals.append(4.0)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            ny, nx = y + dy, x + dx
            if not (0 <= ny < chan.shape[0] and 0 <= nx < chan.shape[1]):
                continue  # 窗口外越界: 视作 Neumann(复制), 系数并入自身
            j = idx[ny, nx]
            if j >= 0:
                rows.append(k); cols.append(j); vals.append(-1.0)
            else:
                b[k] += chan[ny, nx]
    # mask 距窗口边 >=pad(32), 邻居不会越界; 越界分支仅防御
    A = sparse.csr_matrix((vals, (rows, cols)), shape=(n, n))
    sol = spsolve(A, b)
    out = chan.copy()
    out[ys, xs] = sol.astype(chan.dtype)
    return out


def fill_window(win: np.ndarray, mwin: np.ndarray, algo: str) -> np.ndarray:
    """mwin: 255=待修复. TELEA 在大区留放射涂抹痕; 水彩背景=低频纹理,
    调和延拓 (Δf=0 + 邻域 Dirichlet 边界) 填充无方向性伪影."""
    if algo == "telea":
        return cv2.inpaint(win, mwin, INPAINT_RADIUS, cv2.INPAINT_TELEA)
    pad = 32
    wpad = cv2.copyMakeBorder(win, pad, pad, pad, pad, cv2.BORDER_REFLECT)
    mpad = cv2.copyMakeBorder(mwin, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    solve_mask = mpad > 0
    filled = np.dstack([
        _harmonic_fill_channel(wpad[:, :, c].astype(np.float64), solve_mask)
        for c in range(3)
    ]).astype(np.uint8)
    # 修复区与已知区 1px 羽化衔接
    blend = cv2.GaussianBlur(solve_mask.astype(np.float32), (3, 3), 0)
    blend = np.clip(blend * 1.2, 0, 1)[:, :, None]
    merged = (wpad.astype(np.float32) * (1 - blend) + filled.astype(np.float32) * blend)
    merged = np.clip(merged, 0, 255).astype(np.uint8)
    return merged[pad:-pad, pad:-pad]


def inpaint_boxes(img: np.ndarray, boxes: list[tuple[int, int, int, int]], label: str,
                  algo: str = "hier"):
    """逐块处理: 每块取窗口修复, 避免全图 mask 一次算."""
    out = img.copy()
    h, w = img.shape[:2]
    for x0, y0, x1, y1 in boxes:
        # 窗口 = 瑕疵 + 充足干净上下文 (patch-based 需要纹理样本)
        pad = max(int((x1 - x0) * 2.5), int((y1 - y0) * 2.5), 120)
        wx0, wy0 = max(x0 - pad, 0), max(y0 - pad, 0)
        wx1, wy1 = min(x1 + pad, w), min(y1 + pad, h)
        win = out[wy0:wy1, wx0:wx1]
        mwin = np.zeros(win.shape[:2], np.uint8)
        mwin[y0 - wy0:y1 - wy0, x0 - wx0:x1 - wx0] = 255
        mwin = cv2.dilate(
            mwin, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MASK_DILATE * 2 + 1,) * 2)
        )
        fixed = fill_window(win, mwin, algo)
        out[wy0:wy1, wx0:wx1] = fixed
        print(f"  [{label}] clean rect ({x0},{y0})-({x1},{y1}) "
              f"{x1 - x0}x{y1 - y0}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-dir", type=Path, default=None,
                    help="清洗区 4x 放大目检图输出目录")
    ap.add_argument("--src-dir", type=Path, default=SRC_DIR)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--algo", choices=["poisson", "telea"], default="poisson",
                    help="填充算法 (默认 Poisson 线性扩散; telea 留涂抹痕仅调试用)")
    args = ap.parse_args()

    total = 0
    for num, (name, manual_boxes) in IMAGES.items():
        src = args.src_dir / f"{num}.png"
        if not src.exists():
            print(f"ERROR: 源图缺失 {src}", file=sys.stderr)
            return 2
        img = cv2.imread(str(src), cv2.IMREAD_COLOR)
        if img.shape[:2] != (1999, 1123):
            print(f"ERROR: {src} 尺寸异常 {img.shape[:2]}, 期望 (1999,1123)", file=sys.stderr)
            return 2

        print(f"{num} -> {name}:")
        boxes = list(manual_boxes)
        if num == "24":
            for b in detect_white_corners(img):
                print(f"  [24] 检测到白色圆角遮罩 ({b[0]},{b[1]})-({b[2]},{b[3]})")
                boxes.append(b)
        if not boxes:
            print("  无瑕疵待清洗")

        regions = [(b, i) for i, b in enumerate(boxes)]
        out = img
        for b, _ in regions:
            out = inpaint_boxes(out, [b], num, args.algo)

        args.out_dir.mkdir(parents=True, exist_ok=True)
        dst = args.out_dir / f"{name}.webp"
        pil = Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
        pil.save(dst, "WEBP", quality=82, method=6)
        kb = dst.stat().st_size // 1024
        total += kb
        print(f"  wrote {dst.relative_to(REPO)}: {kb} KB")
        if kb > MAX_KB:
            print(f"ERROR: 超过单图上限 {MAX_KB} KB", file=sys.stderr)
            return 3

        if args.check_dir:
            args.check_dir.mkdir(parents=True, exist_ok=True)
            h, w = out.shape[:2]
            for i, (x0, y0, x1, y1) in enumerate(boxes):
                pad = 60
                crop = out[max(y0 - pad, 0):min(y1 + pad, h), max(x0 - pad, 0):min(x1 + pad, w)]
                crop = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
                cv2.imwrite(str(args.check_dir / f"{num}_{name}_r{i}.png"), crop)

    print(f"合计 {total} KB (上限 1200)")
    if total > 1200:
        print("ERROR: 四图合计超 1.2MB", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
