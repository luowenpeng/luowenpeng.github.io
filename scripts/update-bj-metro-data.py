#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""北京轨道交通客流数据 — 双运营单位采集管道（微博正文 + 配图 OCR）。

## 为什么这条管道特别

北京线网由多家单位分别运营，其中只有两家**逐日发布**所辖线路客流：

| 单位 | 微博 | uid | 正文格式 |
|---|---|---|---|
| 北京市地铁运营有限公司（北京地铁公司） | @北京地铁 | 2778292197 | `#客流观察#【昨日客流】YYYY年M月D日（星期X）北京地铁公司所辖运营线路日客运量为N万人次。详情如下` |
| 北京京港地铁有限公司 | @京港地铁 | 2125939111 | `#客流信息#YYYY年M月D日（周X）北京地铁4号线客运量为N万人次；大兴线…；4号线大兴线贯通…；14号线…；16号线…；17号线…` |

其余线路（19号线、燕房线、大兴机场线、西郊线、亦庄T1 等）由其他单位运营，
官方不逐日发布，**因此本页的「合计」只是这两家之和，不是全北京市线网总量**。

## 三个关键口径

1. **总量无需 OCR**：北京地铁正文直接给公司总量；京港正文直接给 5 条线数值，
   求和即公司总量。主图表（两家对比 + 合计）纯文本即可产出。
2. **「4号线大兴线贯通客运量」不计入合计**：京港配图标题只列 5 条线路，
   却画了 6 个柱，多出的「4号线-大兴线」是跨线贯通客流（交叉口径），
   与 4 号线、大兴线的本线客流重叠。单独存为 `through` 字段供参考。
3. **两家齐备才写 `total`**：某天只有一家发布时 `total` 留空、合计折线断开，
   不拿单家数据冒充全量，避免图表出现假暴跌。

## 分线明细（可选 OCR）

北京地铁各线数值只在配图柱状图里。OCR 拿到柱顶数值后按 x 坐标排序，
映射到固定的 19 条线路顺序，并用「19 个数值之和 == 正文总量」做硬校验；
校验不过就跳过分线、保留总量。OCR 不可用/失败不影响主数据。
京港 5 条分线来自正文，无需 OCR。

## 数据源边界

两家官方均自 **2026-05 初** 才开始逐日发布（此前检索为空，非索引限制）。
`searchProfile` 时间窗参数 `starttime`/`endtime` 为**北京时间 epoch**；
数据日 D 的微博发布于 D+1，故检索窗口整体后移一天。

用法：
    python scripts/update-bj-metro-data.py --start 2026-04-25 --end 2026-11-04
    python scripts/update-bj-metro-data.py --backfill-days 14
    python scripts/update-bj-metro-data.py --dry-run --no-ocr

零第三方依赖即可产出总量；分线 OCR 需要 `rapidocr-onnxruntime`。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------- 配置常量

ACCOUNTS = {
    "bjsubway": {"uid": "2778292197", "name": "北京地铁公司", "q": "客流"},
    "bjmtr": {"uid": "2125939111", "name": "京港地铁", "q": "客流"},
}

DATA_JS = os.path.join("docs", "public", "bj-metro-passenger-data.js")
MANUAL_JSON = os.path.join("scripts", "bj-metro-manual.json")
IMG_DIR = os.path.join(".cache", "bj-imgs")
OCR_CACHE = os.path.join(".cache", "bj-ocr.json")

DEFAULT_START = date(2026, 5, 5)   # 两家官方逐日发布的首个数据日
WINDOW_DAYS = 30
MAX_TOLERATED_MISSING = 12
MAX_SUM_DIFF = 0.06          # OCR 分线合计与正文总量的允许误差（万人次）

BJ_TZ = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 北京地铁公司配图柱状图，从左到右固定的 19 条线路顺序。
SUB_LINES = [
    "l1", "l2", "l3", "l5", "l6", "l7", "l8", "l9", "l10", "l11",
    "l12", "l13", "l15", "l18", "changping", "fangshan",
    "capital_airport", "yizhuang", "s1",
]
# 京港地铁正文给出的 5 条线路（贯通项单列，不计入）。
MTR_LINES = ["l4", "daxing", "l14", "l16", "l17"]

LINE_LABELS = {
    "l1": "1号线·八通线", "l2": "2号线", "l3": "3号线", "l4": "4号线",
    "l5": "5号线", "l6": "6号线", "l7": "7号线", "l8": "8号线",
    "l9": "9号线", "l10": "10号线", "l11": "11号线", "l12": "12号线",
    "l13": "13号线", "l14": "14号线", "l15": "15号线", "l16": "16号线",
    "l17": "17号线", "l18": "18号线",
    "daxing": "大兴线", "changping": "昌平线", "fangshan": "房山线",
    "capital_airport": "首都机场线", "yizhuang": "亦庄线", "s1": "S1线",
}

ORDER = (["date", "sub", "mtr", "total", "through"] + MTR_LINES + SUB_LINES)

PUB_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

# ---------------------------------------------------------------- 正则

DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
WEEK_RE = re.compile(r"[（(]\s*(?:星期|周)\s*([一二三四五六日天])\s*[）)]")
SUB_TOTAL_RE = re.compile(r"所辖运营线路日客运量为\s*([0-9]+(?:\.[0-9]+)?)\s*万人次")
MTR_SEG_RE = re.compile(
    r"(4号线大兴线贯通|4号线|大兴线|14号线|16号线|17号线)"
    r"(?:客运量)?为\s*([0-9]+(?:\.[0-9]+)?)\s*(?:万人次|万次|万人)?"
)
NON_DAILY = ("假期", "共计运送", "日均", "累计", "创新高", "突破", "万人次平安出行")


def log(msg: str = "") -> None:
    print(msg, flush=True)


def gh_output(**kw) -> None:
    p = os.environ.get("GITHUB_OUTPUT")
    if not p:
        return
    try:
        with open(p, "a", encoding="utf-8") as f:
            for k, v in kw.items():
                f.write(f"{k}={v}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- HTTP

def http_get(url: str, *, cookie: str = "", referer: str = "https://weibo.com/",
             binary: bool = False, timeout: int = 25):
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*" if not binary else "image/avif,image/webp,image/*,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "identity",
        "Referer": referer,
    }
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw if binary else raw.decode("utf-8", "replace")


def load_cookie(args) -> str:
    if args.cookie_file and os.path.exists(args.cookie_file):
        return open(args.cookie_file, "r", encoding="utf-8").read().strip()
    env = os.environ.get("WEIBO_COOKIE", "").strip()
    if env:
        return env
    for cand in (".cache/weibo-cookie.txt", ".cache/bj-cookie.txt"):
        if os.path.exists(cand):
            return open(cand, "r", encoding="utf-8").read().strip()
    return ""


def strip_tags(t: str) -> str:
    t = re.sub(r"<[^>]+>", "", t or "")
    return html.unescape(t).replace("\u200b", "").replace("\xa0", " ").strip()


def epoch_bj(d: date, end_of_day: bool = False) -> int:
    hh, mm, ss = (23, 59, 59) if end_of_day else (0, 0, 0)
    return int(datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=BJ_TZ).timestamp())


def parse_pub(s: str) -> date | None:
    m = re.match(r"^[A-Za-z]{3}\s+([A-Za-z]{3})\s+(\d{1,2})\s+[\d:]+\s+\+0800\s+(\d{4})", s or "")
    if not m or m.group(1) not in PUB_MONTHS:
        return None
    return date(int(m.group(3)), PUB_MONTHS[m.group(1)], int(m.group(2)))


def fetch_account_posts(cookie: str, key: str, pub_start: date, pub_end: date) -> list[dict]:
    """按 30 天发布日窗口检索某账号的候选微博，逐页翻到不足一页。"""
    cfg = ACCOUNTS[key]
    uid, q = cfg["uid"], cfg["q"]
    seen: dict[str, dict] = {}
    cur = pub_start
    while cur <= pub_end:
        wend = min(cur + timedelta(days=WINDOW_DAYS - 1), pub_end)
        for page in range(1, 6):
            url = (f"https://weibo.com/ajax/statuses/searchProfile?uid={uid}"
                   f"&q={urllib.parse.quote(q)}&page={page}"
                   f"&starttime={epoch_bj(cur)}&endtime={epoch_bj(wend, True)}")
            try:
                payload = json.loads(http_get(url, cookie=cookie,
                                             referer=f"https://weibo.com/u/{uid}"))
            except Exception as exc:
                log(f"  [{key}] {cur} p{page} 检索失败：{exc}")
                break
            if payload.get("ok") != 1:
                log(f"  [{key}] {cur} p{page} ok={payload.get('ok')}（Cookie 可能失效）")
                break
            lst = (payload.get("data") or {}).get("list") or []
            for p in lst:
                mid = str(p.get("mid") or p.get("id") or "")
                if not mid or mid in seen:
                    continue
                pics = p.get("pic_infos") or {}
                img = ""
                if pics:
                    v = next(iter(pics.values()))
                    img = (((v.get("large") or v.get("original") or {}).get("url"))
                           or v.get("url") or "")
                seen[mid] = {
                    "mid": mid,
                    "pub": p.get("created_at", ""),
                    "text": strip_tags(p.get("text_raw") or p.get("text") or ""),
                    "img": img,
                }
            if len(lst) < 20:
                break
            time.sleep(0.15)
        log(f"  [{key}] {cur} ~ {wend} 累计 {len(seen)} 条")
        time.sleep(0.15)
        cur = wend + timedelta(days=1)
    return sorted(seen.values(), key=lambda p: p["pub"])


# ---------------------------------------------------------------- 正文解析

def resolve_date(text: str, pub: date | None):
    """返回 (数据日, 说明)。官方偶有日期标签笔误，用「发布日-1」+ 星期校验纠偏。"""
    m = DATE_RE.search(text)
    if not m:
        return None, "无日期"
    claimed = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if not pub:
        return claimed, ""
    expected = pub - timedelta(days=1)
    if claimed == expected:
        return claimed, ""
    wm = WEEK_RE.search(text)
    wd = WEEKDAYS.get(wm.group(1)) if wm else None
    if wd is not None and expected.weekday() == wd and claimed.weekday() != wd:
        return expected, f"日期标签笔误 {claimed} → {expected}（按发布日-1 与星期校正）"
    return claimed, ""


def parse_bjsubway(text: str, pub: date | None):
    """北京地铁公司：正文直接给所辖线路总量。"""
    if "【昨日客流】" not in text:
        return None, "非每日客流"
    if any(k in text for k in NON_DAILY):
        return None, "非每日"
    m = SUB_TOTAL_RE.search(text)
    if not m:
        return None, "缺总量"
    d, note = resolve_date(text, pub)
    if not d:
        return None, "无日期"
    return {"date": d.isoformat(), "sub": round(float(m.group(1)), 2)}, note


def parse_bjmtr(text: str, pub: date | None):
    """京港地铁：正文给 5 条线 + 1 个贯通参考量，求和得公司总量。"""
    if "#客流信息#" not in text or "客运量为" not in text:
        return None, "非每日客流"
    if any(k in text for k in NON_DAILY):
        return None, "非每日"
    d, note = resolve_date(text, pub)
    if not d:
        return None, "无日期"
    found = {name: float(val) for name, val in MTR_SEG_RE.findall(text)}
    keymap = {"4号线": "l4", "大兴线": "daxing", "14号线": "l14",
              "16号线": "l16", "17号线": "l17",
              "4号线大兴线贯通": "through"}
    rec = {"date": d.isoformat()}
    for zh, key in keymap.items():
        if zh in found:
            rec[key] = round(found[zh], 2)
    if not all(k in rec for k in MTR_LINES):
        return None, "京港线路不齐：" + ",".join(k for k in MTR_LINES if k not in rec)
    rec["mtr"] = round(sum(rec[k] for k in MTR_LINES), 2)
    return rec, note


# ---------------------------------------------------------------- 配图 OCR

def get_ocr():
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR()
    except Exception as exc:
        log(f"  [提示] OCR 引擎不可用（{exc}），跳过北京地铁分线明细，总量不受影响。")
        return None


def load_ocr_cache() -> dict:
    if os.path.exists(OCR_CACHE):
        try:
            return json.load(open(OCR_CACHE, "r", encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_ocr_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(OCR_CACHE), exist_ok=True)
    tmp = OCR_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, OCR_CACHE)


def download_img(item: dict) -> bool:
    dest = os.path.join(IMG_DIR, item["mid"] + ".jpg")
    if os.path.exists(dest) and os.path.getsize(dest) > 1024:
        return True
    if not item.get("img"):
        return False
    try:
        raw = http_get(item["img"], referer="https://weibo.com/", binary=True, timeout=30)
        if len(raw) < 1024:
            return False
        os.makedirs(IMG_DIR, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(raw)
        return True
    except Exception:
        return False


def ocr_sub_lines(engine, item: dict) -> tuple[dict, float] | None:
    """OCR 北京地铁柱状图：按 x 取柱顶数值 → 固定 19 线顺序 → 返回 (字段, 合计)。"""
    path = os.path.join(IMG_DIR, item["mid"] + ".jpg")
    try:
        res, _ = engine(path)
    except Exception:
        return None
    vals = []
    for box, text, _score in (res or []):
        t = (text or "").strip().replace(" ", "")
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", t):
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        if cx < 120 or cy > 830:     # 排除左侧 Y 轴刻度与底部水印
            continue
        vals.append((cx, float(t)))
    vals.sort()
    if len(vals) != len(SUB_LINES):
        return None
    nums = [v for _x, v in vals]
    return dict(zip(SUB_LINES, nums)), round(sum(nums), 2)


def attach_sub_lines(records: dict[str, dict], posts: list[dict], cache: dict) -> tuple[int, int]:
    """给北京地铁已收录的日期补 19 条分线。返回 (成功数, 跳过数)。"""
    wanted = []
    by_mid = {}
    for p in posts:
        pub = parse_pub(p["pub"])
        parsed, _ = parse_bjsubway(p["text"], pub)
        if not parsed:
            continue
        d = parsed["date"]
        rec = records.get(d)
        if rec and "sub" in rec and all(k in rec for k in SUB_LINES):
            continue
        if p["mid"] in cache and cache[p["mid"]] is None:
            continue
        wanted.append(p)
        by_mid[p["mid"]] = (d, parsed["sub"])
    if not wanted:
        return 0, 0
    engine = get_ocr()
    if engine is None:
        return 0, len(wanted)

    log(f"待 OCR 配图 {len(wanted)} 张，先并发下载 …")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        ok = list(ex.map(download_img, wanted))
    todo = [p for p, good in zip(wanted, ok) if good]
    log(f"  下载成功 {len(todo)}/{len(wanted)}，开始识别 …")

    done = skip = 0
    for i, p in enumerate(todo, 1):
        d, official_total = by_mid[p["mid"]]
        if p["mid"] in cache:
            data, s = cache[p["mid"]]
        else:
            got = ocr_sub_lines(engine, p)
            if not got:
                cache[p["mid"]] = None
                skip += 1
                continue
            data, s = got
            cache[p["mid"]] = [data, s]
        if abs(s - official_total) > MAX_SUM_DIFF or d not in records:
            skip += 1
            continue
        records[d].update(data)
        done += 1
        if i % 25 == 0:
            log(f"  已识别 {i}/{len(todo)}（成功 {done}，跳过 {skip}）")
            save_ocr_cache(cache)
    save_ocr_cache(cache)
    return done, skip


# ---------------------------------------------------------------- 合并与写盘

def load_existing() -> list[dict]:
    if not os.path.exists(DATA_JS):
        return []
    try:
        t = open(DATA_JS, "r", encoding="utf-8").read()
        m = re.search(r"var bjMetroPassengerData\s*=\s*(\[.*?\]);", t, re.S)
        if not m:
            return []
        # 兼容早期版本写出的 JS 尾逗号（JS 合法、JSON 不合法）
        return json.loads(re.sub(r",(\s*)\]\s*$", r"\1]", m.group(1)))
    except Exception as exc:
        log(f"[警告] 读取数据文件失败：{exc}")
        return []


def load_manual():
    if not os.path.exists(MANUAL_JSON):
        return [], []
    try:
        obj = json.load(open(MANUAL_JSON, "r", encoding="utf-8"))
        recs = obj.get("records", [])
        return recs, [r for r in recs if r.pop("_override", False)]
    except Exception as exc:
        log(f"[警告] 读取人工基线失败：{exc}")
        return [], []


def merge_field(old: dict, new: dict) -> dict:
    """字段级修补：旧记录缺字段则补；两边都有但值不同时保留旧值。"""
    out = dict(old)
    for k, v in new.items():
        if k not in out or out[k] is None:
            out[k] = v
    return out


def recompute(rec: dict) -> dict:
    """两家齐备才出 total；否则留空（页面断线，不冒充全量）。"""
    if rec.get("sub") is not None and rec.get("mtr") is not None:
        rec["total"] = round(rec["sub"] + rec["mtr"], 2)
    else:
        rec.pop("total", None)
    return rec


def render(records: list[dict]) -> str:
    records = sorted(records, key=lambda r: r["date"])
    head = (
        "// 北京轨道交通客流数据 — 自动生成，请勿手动编辑\n"
        "// 数据来源：微博 @北京地铁（北京市地铁运营有限公司）、@京港地铁（北京京港地铁有限公司）每日客流发布\n"
        "// 口径：sub=北京地铁公司所辖线路总量；mtr=京港地铁 5 线之和；total=两家合计（齐备才有）\n"
        "//       through=4号线-大兴线贯通客运量（交叉口径，不计入 mtr/total）；分线单位均为万人次\n"
        "// 注意：其余运营单位不逐日发布，本页合计小于全北京市线网实际总量\n"
        f"// 记录数：{len(records)} | 日期范围：{records[0]['date']} ~ {records[-1]['date']}\n"
        "var bjMetroPassengerData = [\n"
    ) if records else "// 北京轨道交通客流数据 — 暂无记录\nvar bjMetroPassengerData = [\n"
    # 不加尾逗号：JS 虽允许，但会让 json.loads 解析失败、影响下次增量合并
    body = ",\n".join(
        json.dumps({k: r[k] for k in ORDER if k in r and r[k] is not None},
                   ensure_ascii=False, separators=(",", ":"))
        for r in records
    )
    return head + (body + "\n" if body else "") + "];\n"


def write_atomic(content: str) -> bool:
    tmp = DATA_JS + ".tmp"
    os.makedirs(os.path.dirname(DATA_JS), exist_ok=True)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    with open(tmp, "r", encoding="utf-8") as f:
        assert f.read() == content
    os.replace(tmp, DATA_JS)
    return True


# ---------------------------------------------------------------- main

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="北京双运营单位客流数据采集管道")
    ap.add_argument("--start", help="起始数据日 YYYY-MM-DD（含）")
    ap.add_argument("--end", help="截止数据日 YYYY-MM-DD（含），默认昨天")
    ap.add_argument("--backfill-days", type=int, default=0, help="回溯补录天数")
    ap.add_argument("--dry-run", action="store_true", help="只抓取校验、不写文件")
    ap.add_argument("--no-ocr", action="store_true", help="跳过配图 OCR（总量不受影响）")
    ap.add_argument("--cookie-file", help="微博 Cookie 文本文件路径")
    ap.add_argument("--max-missing", type=int, default=MAX_TOLERATED_MISSING)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    today = date.today()
    end = date.fromisoformat(args.end) if args.end else today - timedelta(days=1)
    if args.backfill_days > 0:
        start = end - timedelta(days=args.backfill_days - 1)
    elif args.start:
        start = date.fromisoformat(args.start)
    else:
        existing = load_existing()
        start = (date.fromisoformat(existing[-1]["date"]) - timedelta(days=6)
                 if existing else DEFAULT_START)
    if start > end:
        log("[错误] 起始日晚于截止日")
        return 1

    cookie = load_cookie(args)
    if not cookie:
        log("[错误] 缺少微博 Cookie：searchProfile 强制需要登录态（本机 .cache/weibo-cookie.txt）")
        gh_output(result="failed", added="0", reason="no-cookie")
        return 1

    # 数据日 D 的微博发布于 D+1 → 检索窗口整体后移一天
    pub_start, pub_end = start + timedelta(days=1), end + timedelta(days=1)
    log(f"目标数据日：{start} ~ {end}")
    log(f"按发布日检索：{pub_start} ~ {pub_end}")

    posts = {k: fetch_account_posts(cookie, k, pub_start, pub_end) for k in ACCOUNTS}
    total_posts = sum(len(v) for v in posts.values())
    log(f"共命中候选微博 {total_posts} 条")

    existing = load_existing()
    manual, forced = load_manual()
    records: dict[str, dict] = {r["date"]: r for r in existing}

    added_dates: set[str] = set()
    notes: list[str] = []
    parsers = {"bjsubway": parse_bjsubway, "bjmtr": parse_bjmtr}
    for key, plist in posts.items():
        valid = 0
        ranked: dict[str, tuple] = {}
        for p in plist:
            pub = parse_pub(p["pub"])
            rec, note = parsers[key](p["text"], pub)
            if rec is None:
                continue
            if note:
                notes.append(f"{key} {note}")
            valid += 1
            # 同日多条：优先发布日离「数据日+1」最近，再比字段更全、最后比发布更晚
            gap = abs(((pub - date.fromisoformat(rec["date"])).days - 1)) if pub else 99
            rank = (-gap, len(rec), p["pub"])
            d = rec["date"]
            if d not in ranked or rank > ranked[d][0]:
                ranked[d] = (rank, rec)
        log(f"  [{key}] 解析出有效日数据 {valid} 条，唯一日期 {len(ranked)}")
        for d, (_rank, rec) in ranked.items():
            if not (start <= date.fromisoformat(d) <= end):
                continue
            if d not in records:
                added_dates.add(d)
                records[d] = rec
            else:
                before = dict(records[d])
                records[d] = merge_field(before, rec)
                if records[d] != before:
                    added_dates.add(d)

    for r in manual:
        d = r["date"]
        if d not in records:
            records[d] = dict(r)
            added_dates.add(d)
        else:
            records[d] = merge_field(records[d], r)
    for r in forced:
        records[r["date"]] = dict(r)
        added_dates.add(r["date"])

    if not args.no_ocr:
        cache = load_ocr_cache()
        done, skip = attach_sub_lines(records, posts["bjsubway"], cache)
        log(f"分线 OCR：成功 {done} 天，跳过 {skip} 天（总量不受影响）")

    for r in records.values():
        recompute(r)

    # 区间完整性校验（按「两家齐备」的天数算缺口，容忍官方停发）
    span = (end - start).days + 1
    complete = sum(1 for d, r in records.items()
                   if start <= date.fromisoformat(d) <= end and "total" in r)
    missing = span - complete
    log(f"区间内两家齐备 {complete}/{span} 天，缺 {missing} 天")
    if missing > args.max_missing:
        log(f"[失败] 缺口 {missing} 天超过上限 {args.max_missing}，不写盘")
        gh_output(result="failed", added="0", reason="too-many-missing")
        return 1

    if notes:
        for n in dict.fromkeys(notes):
            log(f"  [校正] {n}")

    content = render(list(records.values()))
    changed = not os.path.exists(DATA_JS) or open(DATA_JS, "r", encoding="utf-8").read() != content
    if args.dry_run:
        log(f"[演练] 合计 {len(records)} 条，新增/变更 {len(added_dates)} 天，"
            f"范围 {min(records) if records else '-'} ~ {max(records) if records else '-'}")
        log("[演练] --dry-run 已启用，不写文件")
        gh_output(result="success", added=str(len(added_dates)),
                  start_date=str(start), end_date=str(end), total=str(len(records)))
        return 0

    if changed:
        write_atomic(content)
        log(f"已写入 {DATA_JS}")
    else:
        log("数据无变化，不写文件")

    log(f"合计 {len(records)} 条，新增/变更 {len(added_dates)} 天，"
        f"范围 {min(records) if records else '-'} ~ {max(records) if records else '-'}")
    gh_output(result="success", added=str(len(added_dates)),
              start_date=str(start), end_date=str(end), total=str(len(records)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("\n[中断] 用户取消")
        sys.exit(130)
