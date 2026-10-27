# -*- coding: utf-8 -*-
"""生成 1200x630 og:image 默认分享卡（配色与站点主题一致）

用法：
    python scripts/gen-og-image.py                  # 写入 docs/public/og-image.png
    python scripts/gen-og-image.py --out /tmp/x.png # 指定输出路径
"""
import argparse
import os
import sys
from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
BG = "#ffffff"          # 底
BRAND = "#3d5a80"       # 深蓝（主品牌色）
BRAND2 = "#5b7aa2"      # 中蓝
TEXT_DARK = "#213547"   # 近黑正文
TEXT_GRAY = "#8fa9c7"   # 浅灰蓝

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

# 顶部品牌色条
d.rectangle([0, 0, W, 10], fill=BRAND)

# 字体：按平台候选，取第一个存在的中文字体（不再硬编码单一机器的路径）
BOLD_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]
REGULAR_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def pick_font(candidates):
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


F_BOLD_PATH = pick_font(BOLD_CANDIDATES)
F_REG_PATH = pick_font(REGULAR_CANDIDATES)
if not F_BOLD_PATH or not F_REG_PATH:
    sys.exit(
        "未找到可用的中文字体。请用系统字体路径替换 candidates，\n"
        f"已尝试：{BOLD_CANDIDATES + REGULAR_CANDIDATES}"
    )

F_BOLD_XL = ImageFont.truetype(F_BOLD_PATH, 96)
F_MED = ImageFont.truetype(F_REG_PATH, 40)
F_MED_B = ImageFont.truetype(F_BOLD_PATH, 40)
F_SMALL = ImageFont.truetype(F_REG_PATH, 30)

# 左侧竖线装饰
d.rectangle([100, 170, 108, 400], fill=BRAND2)

# 主标题
d.text((140, 165), "Wenpeng LUO", font=F_BOLD_XL, fill=TEXT_DARK)

# 副标题
d.text((142, 300), "雒文鹏 · 城市轨道交通运营咨询顾问", font=F_MED, fill=TEXT_DARK)

# 格言（品牌色，中蓝分隔）
d.text((142, 370), "诚意正心", font=F_MED_B, fill=BRAND)
w1 = d.textlength("诚意正心", font=F_MED_B)
d.text((142 + w1 + 28, 370), "·", font=F_MED_B, fill=BRAND2)
d.text((142 + w1 + 60, 370), "修身齐家", font=F_MED_B, fill=BRAND)

# 底部域名 + 右下角装饰块
d.text((100, 540), "luowenpeng.com", font=F_SMALL, fill=TEXT_GRAY)
d.rectangle([W-160, H-60, W-40, H-40], fill=BRAND2)
d.rectangle([W-110, H-60, W-90, H-40], fill=BG)

# 输出路径：默认仓库内的 docs/public/og-image.png，相对本脚本位置解析，换机器也能跑
DEFAULT_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "public", "og-image.png",
)

parser = argparse.ArgumentParser(description="生成站点 og:image 分享卡")
parser.add_argument("--out", default=DEFAULT_OUT, help="输出文件路径")
args = parser.parse_args()

out = os.path.abspath(args.out)
os.makedirs(os.path.dirname(out), exist_ok=True)
img.save(out, "PNG", optimize=True)
print("saved:", out)
