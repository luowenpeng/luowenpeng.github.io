#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重庆轨道交通客流数据 — 采集管道（微博配图 + OCR）

与西安地铁管道的关键差异
------------------------
西安地铁在微博**正文**里直接写出各线路客流，可纯文本解析；
重庆轨道交通（微博 @重庆轨道交通，UID 2152519810）正文只给线网总量，
分线路数据全部在**每日配图**里，必须：抓图 → OCR → 结构化。

数据源
------
m.weibo.cn 容器接口（需 Cookie）
    https://m.weibo.cn/api/container/getIndex
      ?type=uid&value=2152519810&containerid=1076032152519810&page=N
    每页约 10 条微博，支持翻页，可回溯到任意历史日期。
    Cookie 通过环境变量 WEIBO_COOKIE 或 --cookie-file 提供；
    仅 SUB / SUBP 两项即可（访客态足够，不要求登录）。

OCR
---
rapidocr-onnxruntime（纯 CPU / ONNX，无需 GPU）
    pip install rapidocr-onnxruntime
    实测官方图表识别置信度 ≈ 1.00，单图约 3.5s。

设计约束（沿用西安管道的工程口径）
----------------------------------
1. 幂等：以 date 为主键合并，已存在的日期不覆盖（除非 --overwrite）。
2. 不写脏数据：单条校验不通过即丢弃该条；整批异常则不落盘。
3. 原子写：临时文件 → 回读校验 → os.replace。
4. 图片缓存：已下载并解析成功的图片按 mid 缓存，重跑不重复下载。

用法
----
  python scripts/update-cq-metro-data.py
      # 增量补录到「昨天」，已存在的日期跳过
  python scripts/update-cq-metro-data.py --dry-run
  python scripts/update-cq-metro-data.py --backfill-days 320
      # 首次全量回填（2025-12-29 起约 300 天）
  python scripts/update-cq-metro-data.py --end 2026-10-28

产出
----
  docs/public/cq-metro-passenger-data.js

退出码
------
  0  成功（含「无新数据」）  1  失败（未修改任何文件）
"""
from __future__ import annotations

import argparse
import difflib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

try:  # Windows 控制台中文输出兜底
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------- 配置常量

UID = "2152519810"
CONTAINER = f"107603{UID}"
API = ("https://m.weibo.cn/api/container/getIndex"
       f"?type=uid&value={UID}&containerid={CONTAINER}&page={{page}}")

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")
PC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 免登录源：sina.cn 媒体聚合页（服务端渲染微博全文，免 Cookie）
#   列表页 https://www.sina.cn/media/2152519810  仅返回最近约 101 条（≈23 天）
#   详情页 https://www.sina.cn/news/detail/{id}.html  含配图 og:image
SINA_MEDIA = f"https://www.sina.cn/media/{UID}"
SINA_DETAIL = "https://www.sina.cn/news/detail/{mid}.html"
# 已知历史 detail id → 图片 URL（BFS 挖掘结果的持久化缓存，可手工补充）
SINA_EXTRA = os.path.join(".cache", "cq-metro-known.json")

DATA_JS = os.path.join("docs", "public", "cq-metro-passenger-data.js")
IMG_CACHE = os.path.join(".cache", "cq-metro-img")
OCR_CACHE = os.path.join(".cache", "cq-metro-ocr.json")

# 重庆轨道交通已开通线路（口径：官方日客流统计图，3号线含空港线）
LINES = [
    ("ring", "环线"),
    ("line1", "1号线"),
    ("line2", "2号线"),
    ("line3", "3号线"),
    ("line4", "4号线"),
    ("line5", "5号线"),
    ("line6", "6号线"),
    ("guobo", "国博线"),
    ("line9", "9号线"),
    ("line10", "10号线"),
    ("line18", "18号线"),
    ("jiangtiao", "江跳线"),
    ("bitong", "璧铜线"),
]
ORDER = ["date", "total"] + [k for k, _ in LINES]
KNOWN_LABELS = [lb for _, lb in LINES]

# OCR 常见误识别 → 纠正表（按实际跑批结果持续补充）
OCR_FIXUPS = {
    "工": "江跳线",       # 江跳线在窄列里常被切成单字
    "江跳": "江跳线",
    "璧铜": "璧铜线",
    "壁铜线": "璧铜线",
    "国博": "国博线",
    "环": "环线",
    "l号线": "1号线",
    "１号线": "1号线",
}

MIN_TOTAL, MAX_TOTAL = 80.0, 1000.0
MIN_LINE, MAX_LINE = 0.05, 300.0
MAX_SUM_DIFF = 1.5          # 13 个数各自四舍五入，累计容差放宽到 1.5
MIN_VALID_DATE = date(2024, 1, 1)

MAX_TOLERATED_MISSING = 5
MAX_MISSING_RATIO = 0.12

HTTP_TIMEOUT = 25
HTTP_RETRIES = 3
PAGE_SLEEP = 0.6
MAX_PAGES = 120


class PipelineError(Exception):
    """不可恢复的管道错误 → 退出码 1，不落盘任何脏数据。"""


class SourceUnavailable(PipelineError):
    pass


# ---------------------------------------------------------------- 通用工具


def log(msg: str = "") -> None:
    print(msg, flush=True)


def gh_output(**kv) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in kv.items():
            f.write(f"{k}={v}\n")


def gh_summary(title: str, lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"### {title}\n\n")
        for ln in lines:
            f.write(f"- {ln}\n")
        f.write("\n")


def http_get(url: str, *, cookie: str = "", referer: str = "", binary: bool = False):
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        headers["Cookie"] = cookie
    if referer:
        headers["Referer"] = referer
    last_err: Exception | None = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read()
            return raw if binary else raw.decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < HTTP_RETRIES:
                time.sleep(2 ** (attempt - 1))
    raise SourceUnavailable(f"请求失败 {url}: {last_err}")


def load_cookie(args) -> str:
    """依次尝试：--cookie-file → 环境变量 WEIBO_COOKIE → 默认 cookie 文件。"""
    if args.cookie_file:
        with open(args.cookie_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    env = os.environ.get("WEIBO_COOKIE", "").strip()
    if env:
        return env
    for p in (os.path.join(".cache", "weibo-cookie.txt"), "weibo-cookie.txt"):
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
    return ""  # 无 Cookie 时回退到免登录源


# ---------------------------------------------------------------- 字段校验


def validate_record(rec: dict, today: date) -> tuple[bool, str]:
    try:
        d = date.fromisoformat(rec["date"])
    except Exception:
        return False, "日期格式非法"
    if d < MIN_VALID_DATE:
        return False, f"日期早于下限 {MIN_VALID_DATE}"
    if d > today:
        return False, "日期为未来值"

    total = rec.get("total")
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        return False, "total 非数值"
    if not (MIN_TOTAL <= total <= MAX_TOTAL):
        return False, f"total {total} 超出合理区间"

    for key, _label in LINES:
        v = rec.get(key)
        if v is None:
            return False, f"缺 {key}"
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return False, f"{key} 非数值"
        if not (MIN_LINE <= v <= MAX_LINE):
            return False, f"{key}={v} 超出合理区间"

    ssum = sum(rec[k] for k, _ in LINES)
    if abs(ssum - total) > MAX_SUM_DIFF:
        return False, f"分线和 {ssum:.1f} 与 total {total} 偏差 > {MAX_SUM_DIFF}"
    return True, ""


# ---------------------------------------------------------------- OCR


_ocr = None


def get_ocr():
    global _ocr
    if _ocr is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # pragma: no cover
            raise PipelineError(
                "未安装 OCR 引擎，请先执行：pip install rapidocr-onnxruntime"
            ) from exc
        _ocr = RapidOCR()
    return _ocr


def load_ocr_cache() -> dict:
    if os.path.exists(OCR_CACHE):
        try:
            with open(OCR_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_ocr_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(OCR_CACHE), exist_ok=True)
    tmp = OCR_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, OCR_CACHE)


def ocr_image(path: str, cache: dict, mid: str):
    """OCR 一张图，返回 [(text, box)]。box = [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]。"""
    if mid in cache:
        return [(t, b) for t, b in cache[mid]]
    res, _ = get_ocr()(path)
    items = [(t, b) for b, t, _s in (res or [])]
    cache[mid] = items
    return items


_SIZES = ("thumbnail", "orj360", "orj480", "mw690", "mw1024", "wap240",
          "wap360", "wap720", "square", "small", "bmiddle", "middle")


def to_large(url: str) -> str:
    """把任意尺寸的新浪图床 URL 规范化到原图（large）。"""
    u = re.sub(r"/(?:%s)/" % "|".join(_SIZES), "/large/", url)
    return u.replace("http://", "https://")


def match_label(text: str) -> str | None:
    """把 OCR 出的线路名归一到标准名；无法匹配返回 None。"""
    t = text.strip().replace(" ", "").replace("　", "")
    t = t.replace("號", "号").replace("線", "线")
    if t in OCR_FIXUPS:
        return OCR_FIXUPS[t]
    if t in KNOWN_LABELS:
        return t
    # 去掉尾部标点噪声
    t2 = re.sub(r"[.:：,，。、'`\"|]+$", "", t)
    if t2 in KNOWN_LABELS:
        return t2
    if t2 in OCR_FIXUPS:
        return OCR_FIXUPS[t2]
    # 数字线路：如 "10号线" 被切成 "10号" / "号线"
    m = re.match(r"^(\d{1,2})号?线?$", t2)
    if m and f"{int(m.group(1))}号线" in KNOWN_LABELS:
        return f"{int(m.group(1))}号线"
    near = difflib.get_close_matches(t2, KNOWN_LABELS, n=1, cutoff=0.6)
    return near[0] if near else None


NUM_RE = re.compile(r"^\d{1,4}(?:\.\d{1,2})?$")


def parse_ocr(items) -> dict:
    """把 OCR 结果解析成 {date, total, 各线路}。

    图表版式（固定）：
        标题行
        日期：<YYYY.M.D>        总客流：<num>  单位：万人次
        <线路名> <num>   × 13
        注：…
        @重庆轨道交通
    解析策略：按 y 聚类分行 → 每行内按 x 排序 → 左侧文本为名称，右侧首个数字为值。
    """
    if not items:
        return {}
    # 每行项目：text, x0, x1, cy
    rows_src = []
    for text, box in items:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        rows_src.append({
            "t": text.strip(),
            "x0": min(xs), "x1": max(xs),
            "cy": (min(ys) + max(ys)) / 2,
            "h": max(ys) - min(ys),
        })
    heights = sorted(r["h"] for r in rows_src)
    med_h = heights[len(heights) // 2] or 12
    tol = max(med_h * 0.6, 6)

    rows_src.sort(key=lambda r: r["cy"])
    rows: list[list[dict]] = []
    cur: list[dict] = []
    cur_y = None
    for r in rows_src:
        if cur_y is None or abs(r["cy"] - cur_y) <= tol:
            cur.append(r)
            cur_y = r["cy"] if cur_y is None else (cur_y * (len(cur) - 1) + r["cy"]) / len(cur)
        else:
            rows.append(cur)
            cur = [r]
            cur_y = r["cy"]
    if cur:
        rows.append(cur)

    out: dict = {}
    data_rows: list[float] = []
    for row in rows:
        row.sort(key=lambda r: r["x0"])
        texts = [r["t"] for r in row]
        nums = [r["t"] for r in row if NUM_RE.match(r["t"])]
        joined = "".join(texts)

        # 表头行：日期 / 总客流 / 单位 常挤在同一行（如
        # "日期：2026.10.28 总客流：387.0 单位：万人次"），故不做 continue，
        # 而是各自用带锚点的正则提取，避免把日期数字误当客流。
        if "日期" in joined or "总客流" in joined or "单位" in joined:
            if "date" not in out:
                m = re.search(r"(20\d{2})[.\-/年]\s*(\d{1,2})[.\-/月]\s*(\d{1,2})", joined)
                if m:
                    try:
                        out["date"] = date(int(m.group(1)), int(m.group(2)),
                                           int(m.group(3))).isoformat()
                    except ValueError:
                        pass
            if "total" not in out:
                m = re.search(r"总客流[：:\s]*(\d{1,4}(?:\.\d{1,2})?)", joined)
                if m:
                    out["total"] = float(m.group(1))
            continue

        # 标题 / 注释 / 署名行
        if ("统计" in joined and "线路" in joined) or joined.startswith("注") or "@" in joined:
            continue

        # 线路行
        if not nums:
            continue
        data_rows.append(float(nums[0]))
        label = None
        for r in row:
            lb = match_label(r["t"])
            if lb:
                label = lb
                break
        if label is None:
            # 名称误识别时的兜底：取该行最左侧非数字文本
            cand = [r["t"] for r in row if not NUM_RE.match(r["t"])]
            if cand:
                label = match_label(cand[0])
        if label is None:
            continue
        key = dict((lb, k) for k, lb in LINES)[label]
        if key not in out:
            try:
                out[key] = round(float(nums[0]), 1)
            except ValueError:
                pass

    # 顺序兜底：图中线路排列固定（环线→1→2→…→璧铜线），
    # 名称被 OCR 切坏时按行序回填缺失项
    if len(data_rows) == len(LINES):
        for idx, (key, _label) in enumerate(LINES):
            if key not in out:
                out[key] = round(data_rows[idx], 1)
    return out


# ---------------------------------------------------------------- 微博抓取


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def fetch_posts(cookie: str, start: date, end: date) -> list[dict]:
    """翻页抓取 [start, end] 区间内带「昨日客运量」的微博。"""
    posts: list[dict] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        raw = http_get(API.format(page=page), cookie=cookie, referer="https://m.weibo.cn/")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PipelineError(f"第 {page} 页 JSON 解析失败：{exc}") from exc
        if not payload.get("ok"):
            raise SourceUnavailable(f"第 {page} 页返回 ok={payload.get('ok')}（Cookie 可能失效）")
        cards = payload.get("data", {}).get("cards", [])
        if not cards:
            break

        hit = 0
        oldest = None
        for card in cards:
            mb = card.get("mblog") or {}
            if not mb:
                continue
            created = mb.get("created_at", "")
            try:
                pub = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y").date()
            except Exception:
                continue
            oldest = pub if oldest is None or pub < oldest else oldest
            body = strip_tags(mb.get("text", ""))
            if "昨日客运量" not in body:
                continue
            pics = mb.get("pics") or []
            if not pics:
                continue
            big = pics[0].get("large", {}).get("url") or pics[0].get("url")
            if not big:
                continue
            mid = str(mb.get("mid") or mb.get("id"))
            if mid in seen:
                continue
            seen.add(mid)
            posts.append({
                "mid": mid,
                "pub": pub,
                "text": body,
                "img": big,
            })
            hit += 1

        log(f"  第 {page:>2} 页：命中 {hit} 条，本页最早 {oldest}")
        if oldest and oldest < start - timedelta(days=5):
            break
        if hit == 0 and oldest and oldest < start - timedelta(days=40):
            break
        time.sleep(PAGE_SLEEP)

    return sorted(posts, key=lambda p: p["pub"])


def fetch_sina_posts() -> list[dict]:
    """免登录源：sina.cn 媒体页 → 详情页取配图。

    返回与 fetch_posts 相同结构。受限于列表页只返回最近约 101 条微博，
    该源通常只能覆盖最近 2~3 周；历史回填仍需 Cookie 源。
    """
    log("抓取免登录源 sina.cn …")
    html = http_get(SINA_MEDIA)
    blocks = re.split(r'<a class="post-link" href="/news/detail/', html)[1:]
    posts: list[dict] = []
    for b in blocks:
        mid = b[:b.find(".html")]
        mtime = re.search(r'<div class="time">(\d{4}-\d{2}-\d{2})', b)
        mtxt = re.search(r'<div class="post-text">(.*?)</div>', b, re.DOTALL)
        if not mtime:
            continue
        text = strip_tags(mtxt.group(1)) if mtxt else ""
        if "昨日客运量" not in text:
            continue
        try:
            pub = date.fromisoformat(mtime.group(1))
        except ValueError:
            continue
        posts.append({"mid": mid, "pub": pub, "text": text, "img": ""})
    log(f"  列表页命中 {len(posts)} 条客运量微博")

    # 已知历史条目（BFS 挖掘结果缓存）
    if os.path.exists(SINA_EXTRA):
        try:
            with open(SINA_EXTRA, "r", encoding="utf-8") as f:
                extra = json.load(f)
        except Exception:
            extra = {}
        known = {p["mid"] for p in posts}
        for mid, meta in extra.items():
            if mid in known:
                continue
            try:
                pub = date.fromisoformat(str(meta.get("t", ""))[:10])
            except Exception:
                continue
            posts.append({
                "mid": mid, "pub": pub,
                "text": meta.get("desc", ""),
                "img": meta.get("img", ""),
            })
        log(f"  合并已知历史后共 {len(posts)} 条")

    # 逐条拉详情页取配图
    got = 0
    for p in posts:
        if p["img"]:
            got += 1
            continue
        try:
            dh = http_get(SINA_DETAIL.format(mid=p["mid"]))
        except Exception:
            continue
        m = re.search(r'property="image" content="([^"]+)"', dh)
        if m:
            p["img"] = to_large(m.group(1))
            got += 1
        time.sleep(0.2)
    log(f"  取得配图 {got}/{len(posts)} 张")
    return sorted(posts, key=lambda p: p["pub"])


def text_date_of(post: dict) -> date | None:
    """从正文「#昨日客运量#10月28日」推断数据日期。"""
    m = re.search(r"(\d{1,2})月(\d{1,2})日", post["text"])
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    pub: date = post["pub"]
    try:
        d = date(pub.year, month, day)
    except ValueError:
        return None
    if d > pub:  # 跨年修正
        d = date(pub.year - 1, month, day)
    return d


def text_total_of(post: dict) -> float | None:
    m = re.search(r"(?:客运量|客流)[^\d]{0,6}([\d.]+)\s*万人次", post["text"])
    return float(m.group(1)) if m else None


def download(img_url: str, dest: str) -> bool:
    if os.path.exists(dest) and os.path.getsize(dest) > 1024:
        return True
    try:
        raw = http_get(img_url, referer="https://m.weibo.cn/", binary=True)
    except Exception as exc:  # noqa: BLE001
        log(f"    [下载失败] {exc}")
        return False
    if len(raw) < 1024:
        return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(raw)
    return True


def resolve_records(posts: list[dict], start: date, end: date) -> tuple[list[dict], list[tuple[str, str]]]:
    """下载图片 → OCR → 解析 → 校验。返回 (合规记录, 被丢弃项)。"""
    todo = [p for p in posts if start <= (p["pub"] - timedelta(days=1)) <= end]
    if not todo:
        return [], []
    log(f"待处理图片 {len(todo)} 张，开始下载 …")
    os.makedirs(IMG_CACHE, exist_ok=True)
    targets = []
    for p in todo:
        p["img"] = to_large(p["img"])
        ext = os.path.splitext(p["img"])[1] or ".jpg"
        dest = os.path.join(IMG_CACHE, f"{p['mid']}{ext}")
        targets.append((p, dest))
    ok = 0
    with ThreadPoolExecutor(max_workers=5) as ex:
        for (p, dest), good in zip(targets, ex.map(lambda t: download(t[0]["img"], t[1]), targets)):
            if good:
                ok += 1
    log(f"  下载完成 {ok}/{len(targets)}")

    cache = load_ocr_cache()
    records: list[dict] = []
    dropped: list[tuple[str, str]] = []
    today = date.today()

    for idx, (p, dest) in enumerate(targets, 1):
        if not os.path.exists(dest):
            dropped.append((p["mid"], "图片下载失败"))
            continue
        try:
            items = ocr_image(dest, cache, p["mid"])
        except Exception as exc:  # noqa: BLE001
            log(f"  [OCR 失败] {p['mid']}: {exc}")
            dropped.append((p["mid"], f"OCR 异常 {exc}"))
            continue
        parsed = parse_ocr(items)

        d_iso = parsed.get("date")
        if not d_iso:
            td = text_date_of(p)
            dropped.append((str(td or p["mid"]), "图中未识别到日期"))
            continue
        d = date.fromisoformat(d_iso)
        if not (start <= d <= end):
            continue

        rec = {"date": d_iso}
        miss = [k for k, _ in LINES if k not in parsed]

        # 兜底 1：恰好缺 1 条线路时，由「总量 − 已知分线和」反推
        # （官方图中 total 恰为各线之和，实测 2026-10-28 完全相等）
        if len(miss) == 1:
            key = miss[0]
            hint = parsed.get("total")
            if hint is None:
                hint = text_total_of(p)
            if hint is not None:
                known_sum = sum(parsed[k] for k, _ in LINES if k in parsed)
                val = round(float(hint) - known_sum, 1)
                if MIN_LINE <= val <= MAX_LINE:
                    parsed[key] = val
                    log(f"  [反推] {d_iso} {key}={val}（由总量差值推算）")
                    miss = []

        if miss:
            dropped.append((d_iso, "缺线路：" + ",".join(miss)))
            continue
        for k, _ in LINES:
            rec[k] = parsed[k]

        # total：图内优先，正文兜底
        total = parsed.get("total")
        tm = text_total_of(p)
        if total is None:
            if tm is None:
                dropped.append((d_iso, "未识别到总客流"))
                continue
            total = tm
        elif tm is not None and abs(tm - total) > 0.15:
            log(f"  [提示] {d_iso} 图内总量 {total} 与正文 {tm} 不一致，取图内值")
        rec["total"] = round(float(total), 1)

        ok_flag, reason = validate_record(rec, today)
        if not ok_flag:
            dropped.append((d_iso, reason))
            continue
        records.append({k: rec[k] for k in ORDER})
        if idx % 20 == 0:
            log(f"    已解析 {idx}/{len(targets)}")

    save_ocr_cache(cache)
    best: dict[str, dict] = {}
    for r in records:
        best.setdefault(r["date"], r)
    return sorted(best.values(), key=lambda x: x["date"]), dropped


# ---------------------------------------------------------------- 数据读写


def load_existing() -> list[dict]:
    if not os.path.exists(DATA_JS):
        return []
    with open(DATA_JS, "r", encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"var cqMetroPassengerData = (\[.*\]);", content, re.DOTALL)
    if not m:
        raise PipelineError("数据文件格式错误：未匹配到 var cqMetroPassengerData = [...];")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"数据文件 JSON 解析失败：{exc}") from exc


def render_data_js(records: list[dict]) -> str:
    return (
        "// 重庆轨道交通客流数据 — 自动生成，请勿手动编辑\n"
        "// 数据来源：微博 @重庆轨道交通 每日「昨日客运量」配图（OCR 识别）\n"
        f"// 记录数：{len(records)} | 日期范围：{records[0]['date']} ~ {records[-1]['date']}\n"
        "var cqMetroPassengerData = " + json.dumps(records, ensure_ascii=False, indent=2) + ";\n"
    )


def write_atomic(records: list[dict]) -> None:
    payload = render_data_js(records)
    if not records:
        raise PipelineError("记录集为空，拒绝写入")
    json.loads(re.search(r"var cqMetroPassengerData = (\[.*\]);", payload, re.DOTALL).group(1))
    for rec in records:
        for k in ORDER:
            if k not in rec:
                raise PipelineError(f"写入前校验失败：{rec.get('date')} 缺字段 {k}")
    tmp = DATA_JS + ".tmp"
    os.makedirs(os.path.dirname(DATA_JS), exist_ok=True)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(payload)
    with open(tmp, "r", encoding="utf-8") as f:
        if f.read() != payload:
            os.unlink(tmp)
            raise PipelineError("回读校验不一致，已放弃写入")
    os.replace(tmp, DATA_JS)


# ---------------------------------------------------------------- 主流程


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="重庆轨道交通客流数据采集管道")
    ap.add_argument("--end", help="截止日期 YYYY-MM-DD（含），默认昨天")
    ap.add_argument("--start", help="起始日期 YYYY-MM-DD（含），默认 2025-12-29 或现有数据末日")
    ap.add_argument("--backfill-days", type=int, default=0, help="回溯补录天数")
    ap.add_argument("--dry-run", action="store_true", help="只抓取校验，不写文件")
    ap.add_argument("--overwrite", action="store_true", help="冲突时覆盖（默认保留原值）")
    ap.add_argument("--cookie-file", help="微博 Cookie 文本文件路径")
    ap.add_argument("--source", choices=["auto", "sina", "weibo"], default="auto",
                    help="数据源：auto=双源合并，sina=免登录，weibo=需 Cookie 的全历史")
    ap.add_argument("--max-missing", type=int, default=MAX_TOLERATED_MISSING)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    today = date.today()
    end = date.fromisoformat(args.end) if args.end else today - timedelta(days=1)
    if end > today:
        log(f"[错误] 截止日期 {end} 晚于今天 {today}")
        return 1

    cookie = load_cookie(args)
    if args.source == "weibo" and not cookie:
        log("[错误] --source weibo 需要 Cookie，请用 --cookie-file 或环境变量 WEIBO_COOKIE 提供")
        return 1
    existing = load_existing()
    by_date = {r["date"]: r for r in existing}
    log(f"现有数据：{len(existing)} 条" + (f"，范围 {existing[0]['date']} ~ {existing[-1]['date']}" if existing else ""))

    DEFAULT_START = date(2025, 12, 29)
    if args.backfill_days > 0:
        start = end - timedelta(days=args.backfill_days - 1)
    elif args.start:
        start = date.fromisoformat(args.start)
    elif existing:
        start = date.fromisoformat(existing[-1]["date"])
    else:
        start = DEFAULT_START
    log(f"目标区间：{start} ~ {end}")

    posts: list[dict] = []
    if args.source in ("auto", "sina"):
        posts = fetch_sina_posts()
    if args.source in ("auto", "weibo") and cookie:
        try:
            extra = fetch_posts(cookie, start, end)
        except PipelineError as exc:
            log(f"  [警告] Cookie 源不可用：{exc}")
            extra = []
        if extra:
            merged = {p["mid"]: p for p in posts}
            for p in extra:
                merged.setdefault(p["mid"], p)
            posts = sorted(merged.values(), key=lambda p: p["pub"])
    if not posts and args.source == "auto" and not cookie:
        log("[提示] 未提供 Cookie，仅能覆盖 sina.cn 最近窗口；"
            "配置 Cookie 后可一次性回填全部历史。")
    log(f"共命中带图客流微博 {len(posts)} 条")
    if not posts:
        log("[错误] 未取到任何微博，Cookie 可能失效或账号结构变更")
        gh_output(result="failed", added="0", reason="no-posts")
        return 1

    recs, dropped = resolve_records(posts, start, end)
    for dst, reason in dropped[:30]:
        log(f"  [丢弃] {dst}：{reason}")
    if len(dropped) > 30:
        log(f"  … 另有 {len(dropped) - 30} 条被丢弃")
    log(f"解析出有效记录 {len(recs)} 条")
    if not recs:
        log("[错误] 未解析到任何有效记录，终止且不写入")
        gh_output(result="failed", added="0", reason="no-valid-records")
        return 1

    added, conflicts, skipped = [], [], 0
    for rec in recs:
        old = by_date.get(rec["date"])
        if old is None:
            added.append(rec)
        elif all(old.get(k) == rec.get(k) for k in ORDER[1:]):
            skipped += 1
        else:
            conflicts.append(rec["date"])
            if args.overwrite:
                by_date[rec["date"]] = rec
                added.append(rec)

    if skipped:
        log(f"已存在且一致，跳过 {skipped} 天")
    if conflicts:
        log(f"[警告] {len(conflicts)} 天数据与仓库不一致"
            + ("（--overwrite 已覆盖）" if args.overwrite else "（保留仓库原值）")
            + f"：{conflicts[:10]}")

    if added:
        by_date.update({r["date"]: r for r in added})
    merged = sorted(by_date.values(), key=lambda x: x["date"])

    for rec in merged:
        ok_flag, reason = validate_record(rec, today)
        if not ok_flag:
            log(f"[错误] 合并后校验失败 {rec.get('date')}：{reason}")
            gh_output(result="failed", added="0", reason="post-merge-validation")
            return 1

    have = {r["date"] for r in merged}
    missing, cur = [], start
    while cur <= end:
        if cur.isoformat() not in have:
            missing.append(cur.isoformat())
        cur += timedelta(days=1)
    span = max((end - start).days + 1, 1)
    if missing:
        ratio = len(missing) / span
        tolerated = len(missing) <= args.max_missing or ratio <= MAX_MISSING_RATIO
        log(f"区间内缺失 {len(missing)}/{span} 天（{ratio:.1%}）— {'容忍（多为官方停发）' if tolerated else '超限'}")
        log(f"  缺失日期：{missing[:25]}{' …' if len(missing) > 25 else ''}")
        if not tolerated:
            # 免登录源天然覆盖不到历史，属预期内的「能力边界」，不算采集异常
            limited = args.source == "sina" or (args.source == "auto" and not cookie)
            if limited:
                log("  [提示] 免登录源无法覆盖该区间，属预期范围；"
                    "配置微博 Cookie 后可完整回填。")
            else:
                log("[错误] 缺口超出容忍阈值，判定为采集异常，终止且不写入")
                gh_output(result="failed", added="0", reason="gap-exceeded", missing=len(missing))
                return 1

    if not added:
        log("\n无新增数据。")
        gh_output(result="noop", added="0", total=len(merged), missing=len(missing))
        return 0

    s_date, e_date = added[0]["date"], added[-1]["date"]
    log(f"\n新增 {len(added)} 天：{s_date} ~ {e_date}")

    if args.dry_run:
        log("[dry-run] 校验全部通过，未写入文件。")
        for r in added[:8]:
            log(f"  {r['date']}  total={r['total']}")
        gh_output(result="dry-run", added=len(added), total=len(merged))
        return 0

    try:
        write_atomic(merged)
    except PipelineError as exc:
        log(f"[错误] 写入失败：{exc}")
        gh_output(result="failed", added="0", reason="write-failed")
        return 1
    log(f"已写入 {DATA_JS}：{len(merged)} 条（{merged[0]['date']} ~ {merged[-1]['date']}）")

    gh_output(result="success", added=len(added), start_date=s_date, end_date=e_date,
              total=len(merged), missing=len(missing),
              full_start=merged[0]["date"], full_end=merged[-1]["date"])
    gh_summary("重庆轨道交通客流 — 采集完成", [
        f"新增 **{len(added)}** 天：`{s_date}` ~ `{e_date}`",
        f"数据文件现有 **{len(merged)}** 条：`{merged[0]['date']}` ~ `{merged[-1]['date']}`",
        f"区间内缺失 {len(missing)} 天",
    ])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PipelineError as exc:
        log(f"[致命错误] {exc}")
        gh_output(result="failed", added="0", reason="pipeline-error")
        sys.exit(1)
    except KeyboardInterrupt:
        log("\n已中断")
        sys.exit(130)
