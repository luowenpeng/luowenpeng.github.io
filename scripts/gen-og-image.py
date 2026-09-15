# -*- coding: utf-8 -*-
"""生成 1200x630 og:image 默认分享卡（配色与站点主题一致）"""
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

# 字体（微软雅黑）
F_BOLD_XL = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 96)
F_MED     = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 40)
F_MED_B   = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 40)
F_SMALL   = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 30)

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

out = r"D:\Workbuddy\2026-05-27-13-30-59\luowenpeng.github.io\docs\public\og-image.png"
img.save(out, "PNG", optimize=True)
print("saved:", out)
