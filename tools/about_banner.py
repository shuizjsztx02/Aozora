# T15-6 · 关于页横幅生成（去 tagline，仅保留天空区域）
# 源图：.scratch/t14/about.png（1408×3104 原始截图）
# 裁切：排除顶部状态栏、左侧返回箭头、下方 tagline 区域
# 输出：app/src/assets/about-banner.webp（750px 宽）
import os
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCR = os.path.join(ROOT, '.scratch/t14')
OUT = os.path.join(ROOT, 'app/src/assets/about-banner.webp')

SRC = os.path.join(SCR, 'about.png')
about = Image.open(SRC).convert('RGB')
print('source:', about.size)

# 裁掉：顶 ~150px（状态栏）、左 ~160px（返回箭头）、底 ~2400px（tagline 及以下）
banner = about.crop((160, 150, 1408, 700))
print('cropped:', banner.size)

w_out = 750
h_out = banner.size[1] * w_out // banner.size[0]
banner = banner.resize((w_out, h_out), Image.LANCZOS)

banner.save(OUT, 'WEBP', quality=85, method=6)
print(f'saved {OUT}  ({os.path.getsize(OUT)//1024} KB)')
