#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""郑州地铁客流数据采集管道

数据源：微博 @郑州地铁（uid=1993390131，自定义域名 zhengzhouditie）每日
「YYYY年MM月DD日，郑州市轨道交通线网总客运量…」长微博。

与另外三条管道的差异（这是本脚本存在的理由）：
  * 西安：分线数值在正文里，直接正则；
  * 重庆：分线数值只在配图里，要下载图片 + OCR；
  * 南京：分线数值在正文里，但**列表接口返回的是全文**；
  * 郑州：分线数值在正文里，但**每条都是长微博，列表接口只给前 187 字**
    （`isLongText=True`，末尾 `... 展开`），最后一条「城郊线」必定被截断。
    所以必须对每条额外请求 longtext 接口展开，否则每天都会少一条线路、少约 10 万人次。

正文样例（展开后的全文，2026-11-01）：
    2026年11月01日，郑州市轨道交通线网总客运量261.49万人次（1号线49.12万人次、
    2号线38.10万人次、…、郑许线5.19万人次、城郊线16.23万人次）。

接口实测结论（2026-11-04）：
  * 检索：`weibo.com/ajax/statuses/searchProfile` + `starttime`/`endtime`（**北京时间** epoch）
    参数名必须是 `starttime`/`endtime`，`start_time`/`timescope` 会被静默忽略。
  * 该账号有 6 万余条微博，`mymblog` 翻页与无时间窗的搜索结果都是乱序、且索引只到约 9 页，
    **不能靠翻页回溯**；只能按 30 天一窗逐窗枚举。
  * 微博搜索索引只保留约 12 个月，故本数据集最早只能到 2025-11 前后。
  * 展开：`weibo.com/ajax/statuses/longtext?id=<mblogid>` → `data.longTextContent`（需 Cookie）。
    备用 `m.weibo.cn/statuses/extend?id=<id>` 与 `m.weibo.cn/statuses/show?id=<id>`，
    这两个**免 Cookie 也能拿到全文**，但必须带 `Accept-Encoding: identity` + `MWeibo-Pwa: 1`
    （缺了会返回压缩/非 JSON 内容，容易被误判为接口不可用）。
  * 即便展开可免登录，**检索 `searchProfile` 强制需要 Cookie**（免登录返回 `ok=-100`），
    `m.weibo.cn/api/container/getIndex` 免登录返回 432、`sina.cn` 源同样只给截断正文且仅覆盖约 5 天。
    故整条管道必须有 Cookie，但没有免登录退路。日常周更由本机
    `scripts/local-zz-metro-sync.py` 读取 `.cache/weibo-cookie.txt` 提供，
    凭据不出本机；本脚本在云端应急手动运行时才用 WEIBO_COOKIE 环境变量。

设计要点（沿用西安/重庆/南京三条管道已验证的做法）：
  * 幂等：按日期合并，一致则跳过；字段级修补，只有真值不同才算冲突；
  * 原子写：临时文件 → 回读校验 → os.replace，中途失败不留半个文件；
  * 写盘判据用「最终内容是否与磁盘一致」，不能用「有没有新增数据」；
  * 同日多条按「发布日离数据日+1 最近」优先（官方有日期标签笔误，不能简单取最晚）；
  * 允许缺口：官方停发的日子就是没有，不因此整批回滚。

用法（日常周更走本机包装脚本，下面这些用于诊断/回填）：
    python scripts/local-zz-metro-sync.py                       # 本机一键采集并推送
    python scripts/update-zz-metro-data.py --backfill-days 14   # 只采集，不提交
    python scripts/update-zz-metro-data.py --start 2025-11-01 --end 2026-11-03 --max-missing 60
    python scripts/update-zz-metro-data.py --backfill-days 14 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

try:  # Windows 控制台中文输出兜底
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------- 配置常量

UID = "1993390131"                      # 微博 @郑州地铁 / weibo.com/zhengzhouditie
ACCOUNT = "郑州地铁"
TAG = "总客运量"

SEARCH_API = ("https://weibo.com/ajax/statuses/searchProfile"
              f"?uid={UID}&q={{q}}&page={{page}}")
LONGTEXT_API = "https://weibo.com/ajax/statuses/longtext?id={mid}"
M_EXTEND_API = "https://m.weibo.cn/statuses/extend?id={id}"
M_SHOW_API = "https://m.weibo.cn/statuses/show?id={id}"

DATA_JS = os.path.join("docs", "public", "zz-metro-passenger-data.js")
MANUAL_JSON = os.path.join("scripts", "zz-metro-manual.json")
# 长微博全文缓存：首次全量回填要展开 300+ 条，缓存后可断点续传、避免反复打接口。
CACHE_JSON = os.path.join(".cache", "zz-longtext.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 郑州已开通并单列的线路（近一年内格式固定为这 13 条）。
LINES = [
    ("line1", "1号线"),
    ("line2", "2号线"),
    ("line3", "3号线"),
    ("line4", "4号线"),
    ("line5", "5号线"),
    ("line6", "6号线"),
    ("line7", "7号线"),
    ("line8", "8号线"),
    ("line10", "10号线"),
    ("line12", "12号线"),
    ("line14", "14号线"),
    ("zhengxu", "郑许线"),
    ("chengjiao", "城郊线"),
]
ORDER = ["date", "total"] + [k for k, _ in LINES]
KEY_OF_LABEL = {lb: k for k, lb in LINES}

# 数值合理区间（万人次）。
MIN_TOTAL, MAX_TOTAL = 20.0, 600.0
MIN_LINE, MAX_LINE = 0.0, 200.0
MAX_SUM_DIFF = 0.8          # 13 个两位小数累加的舍入容差；超过就说明正文没展开全
# 正常列全 13 条；但 2026-08-11~13 官方未单列「郑许线」，只列 12 条且
# 分线合计恰好等于总量（181.22 / 171.09 / 206.13），属真实口径变化，应当收录。
# 真正的截断（少「城郊线」）会被下面的「分线合计 == 总量」硬校验拦住，
# 另有一道「结尾含展开」的检查，所以这里放宽到 12 条是安全的。
MIN_LINES_PER_DAY = 12

MIN_VALID_DATE = date(2024, 1, 1)
DEFAULT_START = date(2025, 11, 1)   # 微博搜索索引约 12 个月，再早检索不到

MAX_TOLERATED_MISSING = 6
MAX_MISSING_RATIO = 0.12

HTTP_TIMEOUT = 25
HTTP_RETRIES = 3
PAGE_SLEEP = 0.3
EXPAND_SLEEP = 0.12         # 逐条展开的节流间隔
WINDOW_DAYS = 30
MAX_WINDOW_PAGES = 6
CACHE_FLUSH_EVERY = 25

BJ_TZ = timezone(timedelta(hours=8))

# ---------------------------------------------------------------- 正则

DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
TOTAL_RE = re.compile(r"总客运量\s*([0-9]+(?:\.[0-9]+)?)\s*万人次")
LINE_RE = re.compile(r"(\d{1,2}号线|郑许线|城郊线)\s*([0-9]+(?:\.[0-9]+)?)\s*万人次")


class PipelineError(Exception):
    """不可恢复的管道错误 → 退出码 1，不落盘任何脏数据。"""


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


def http_get(url: str, *, cookie: str = "", referer: str = "", mobile: bool = False) -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        # m.weibo.cn 在默认头下会返回压缩/非 JSON 内容，必须显式要求明文
        "Accept-Encoding": "identity",
    }
    if cookie:
        headers["Cookie"] = cookie
    if referer:
        headers["Referer"] = referer
    if mobile:
        headers["MWeibo-Pwa"] = "1"
        headers["X-Requested-With"] = "XMLHttpRequest"
    last_err: Exception | None = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < HTTP_RETRIES:
                time.sleep(1.2 * attempt)
    raise PipelineError(f"GET 失败（{HTTP_RETRIES} 次）：{url} — {last_err}")


def load_cookie(args) -> str:
    if args.cookie_file and os.path.exists(args.cookie_file):
        with open(args.cookie_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    env = os.environ.get("WEIBO_COOKIE", "").strip()
    if env:
        return env
    for cand in (".cache/weibo-cookie.txt", ".cache/zz-cookie.txt"):
        if os.path.exists(cand):
            with open(cand, "r", encoding="utf-8") as f:
                txt = f.read().strip()
            if txt:
                log(f"  使用本地 Cookie 文件：{cand}")
                return txt
    return ""


def strip_tags(text: str) -> str:
    return (re.sub(r"<[^>]+>", "", text or "")
            .replace("&nbsp;", " ").replace("&amp;", "&").strip())


def epoch_bj(d: date, *, end_of_day: bool = False) -> int:
    """北京时间的 0 点 / 23:59:59 对应的时间戳。

    CI 跑在 UTC，必须显式带 +08:00，否则时间窗整体偏 8 小时。
    """
    hh, mm, ss = (23, 59, 59) if end_of_day else (0, 0, 0)
    return int(datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=BJ_TZ).timestamp())


# ---------------------------------------------------------------- 长微博缓存


def load_cache() -> dict[str, str]:
    if not os.path.exists(CACHE_JSON):
        return {}
    try:
        with open(CACHE_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        log(f"  [提示] 长文缓存读取失败，忽略：{exc}")
        return {}


def save_cache(cache: dict[str, str]) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_JSON), exist_ok=True)
        tmp = CACHE_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, CACHE_JSON)
    except Exception as exc:  # noqa: BLE001
        log(f"  [提示] 长文缓存写入失败（不影响本次采集）：{exc}")


def expand_longtext(post: dict, cookie: str) -> str | None:
    """展开一条长微博，返回全文；三个接口依次降级，全失败返回 None。

    展开后的全文以「…万人次）。」结尾，列表页的截断文本则以「…展开」结尾。
    """
    mblogid = str(post.get("blogid") or post.get("mid") or "")
    wid = str(post.get("id") or post.get("mid") or "")
    ref = f"https://weibo.com/u/{UID}"
    attempts = [
        (LONGTEXT_API.format(mid=mblogid), ref, False, "longTextContent"),
        (M_EXTEND_API.format(id=wid), f"https://m.weibo.cn/status/{wid}", True, "longTextContent"),
        (M_SHOW_API.format(id=wid), f"https://m.weibo.cn/status/{wid}", True, "text"),
    ]
    for url, referer, mobile, field in attempts:
        try:
            payload = json.loads(http_get(url, cookie=cookie, referer=referer, mobile=mobile))
            data = payload.get("data") or {}
            if isinstance(data, dict):
                txt = strip_tags(str(data.get(field) or ""))
                if payload.get("ok") == 1 and txt and "总客运量" in txt:
                    return txt
        except (PipelineError, json.JSONDecodeError, ValueError):
            continue
    return None


def ensure_full_text(posts: list[dict], cookie: str, refresh: bool = False) -> tuple[int, int]:
    """逐条把截断正文替换成全文。返回 (展开成功数, 失败数)。"""
    cache = load_cache()
    ok = fail = 0
    dirty = 0
    for i, p in enumerate(posts, 1):
        key = str(p.get("blogid") or p.get("mid") or p.get("id") or "")
        if not refresh and key and key in cache:
            p["text"] = cache[key]
            continue
        if not p.get("is_long"):
            # 未折叠的短文本本身就是全文，直接用
            cache[key] = p["text"]
            dirty += 1
            continue
        txt = expand_longtext(p, cookie)
        if txt:
            p["text"] = txt
            cache[key] = txt
            ok += 1
            dirty += 1
            time.sleep(EXPAND_SLEEP)
        else:
            fail += 1
        if i % CACHE_FLUSH_EVERY == 0 and dirty:
            save_cache(cache)
            dirty = 0
    if dirty:
        save_cache(cache)
    return ok, fail


# ---------------------------------------------------------------- 正文解析


def parse_text(text: str, pub: date) -> tuple[dict | None, str]:
    """把展开后的全文解析成 {"date","total",<lineKey>:...}，失败返回 (None, 原因)。"""
    body = strip_tags(text)

    # 列表页截断文本以「… 展开」结尾且没有右括号。宁可少一天，也不能把
    # 「城郊线」被砍掉的记录当成完整数据入库（那样分线合计会少约 10 万）。
    if "展开" in body[-12:]:
        return None, "正文未展开"
    if TAG not in body or "万人次" not in body:
        return None, "非客流微博"

    md = DATE_RE.search(body)
    if not md:
        return None, "缺日期"
    try:
        d = date(int(md.group(1)), int(md.group(2)), int(md.group(3)))
    except ValueError:
        return None, "日期非法"
    # 客流为次日 09:00 发布；容忍 0~3 天的延迟（节假日补发）
    if not (pub - timedelta(days=4) <= d <= pub):
        return None, f"日期 {d} 与发布日 {pub} 不符"

    mt = TOTAL_RE.search(body)
    if not mt:
        return None, "缺总量"
    total = round(float(mt.group(1)), 2)
    if not (MIN_TOTAL <= total <= MAX_TOTAL):
        return None, f"总量 {total} 越界"

    rec: dict = {"date": d.isoformat(), "total": total}
    for label, num in LINE_RE.findall(body):
        key = KEY_OF_LABEL.get(label)
        if key is None or key in rec:
            continue
        v = round(float(num), 2)
        if MIN_LINE <= v <= MAX_LINE:
            rec[key] = v

    n = len(rec) - 2
    if n < MIN_LINES_PER_DAY:
        return None, f"仅解析到 {n} 条线路（正常为 {len(LINES)} 条，多半是正文没展开全）"

    # 郑州格式固定列全 13 条线路，分线合计必须与总量自洽。
    # 与南京不同：这里不「放行并留警告」，而是直接拒收，防止把截断数据写进仓库。
    present = sum(v for k, v in rec.items() if k not in ("date", "total"))
    diff = abs(present - total)
    if diff > MAX_SUM_DIFF:
        return None, f"分线合计 {present:.2f} 与总量 {total:.2f} 差 {diff:.2f}"
    return rec, ""


# ---------------------------------------------------------------- 数据抓取


def _collect(lst: list[dict], posts: list[dict], seen: set[str]) -> int:
    """从一页搜索结果里挑出候选客流微博（正文此时仍是截断的）。"""
    hit = 0
    for mb in lst:
        try:
            pub = datetime.strptime(mb.get("created_at", ""), "%a %b %d %H:%M:%S %z %Y").date()
        except Exception:
            continue
        raw = strip_tags(mb.get("text_raw") or mb.get("text") or "")
        if TAG not in raw and "客运量" not in raw:
            continue
        mid = str(mb.get("mid") or mb.get("id") or "")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        posts.append({
            "mid": mid,
            "id": str(mb.get("id") or mid),
            "blogid": str(mb.get("mblogid") or ""),
            "pub": pub,
            "text": raw,
            "is_long": bool(mb.get("isLongText")) or "展开" in raw,
        })
        hit += 1
    return hit


def fetch_window_posts(cookie: str, start: date, end: date) -> list[dict]:
    """按 30 天一窗检索 searchProfile（q=总客运量）。

    该账号发博量大、无时间窗的搜索结果乱序且索引只到约 9 页，
    因此时间窗枚举是唯一可靠的回溯方式。参数名必须是 starttime/endtime。
    """
    log(f"按时间窗检索 searchProfile（q={TAG}，{WINDOW_DAYS} 天一窗）…")
    posts: list[dict] = []
    seen: set[str] = set()
    cur = start
    while cur <= end:
        wend = min(cur + timedelta(days=WINDOW_DAYS - 1), end)
        s_ts, e_ts = epoch_bj(cur), epoch_bj(wend, end_of_day=True)
        got = 0
        for page in range(1, MAX_WINDOW_PAGES + 1):
            url = (f"https://weibo.com/ajax/statuses/searchProfile?uid={UID}"
                   f"&q={urllib.parse.quote(TAG)}&page={page}"
                   f"&starttime={s_ts}&endtime={e_ts}")
            try:
                payload = json.loads(http_get(url, cookie=cookie,
                                              referer=f"https://weibo.com/u/{UID}"))
            except (PipelineError, json.JSONDecodeError) as exc:
                log(f"  {cur}~{wend} 第 {page} 页失败：{exc}")
                break
            lst = (payload.get("data") or {}).get("list") or []
            if not lst:
                break
            before = len(posts)
            got += _collect(lst, posts, seen)
            if len(posts) - before < 1 and page > 1:
                break
            time.sleep(PAGE_SLEEP)
        log(f"  {cur} ~ {wend}：新增 {got} 条（累计 {len(posts)}）")
        cur = wend + timedelta(days=1)
    return posts


# ---------------------------------------------------------------- 记录合并


def to_records(posts: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    """逐条解析 → 按日期去重。

    同日多条时按「发布日离数据日+1 最近」优先，再比线路更全、最后比谁更晚。
    官方是次日早晨发昨日数据，权威出处必是次日那条；直接取「发布最晚」会在
    官方沿用前一天日期标签的笔误日里选错值（南京管道已踩过这个坑）。
    """
    by_date: dict[str, dict] = {}
    by_meta: dict[str, tuple[int, int, object]] = {}
    dropped: list[tuple[str, str]] = []
    for p in posts:
        rec, why = parse_text(p["text"], p["pub"])
        if rec is None:
            if why != "非客流微博":
                dropped.append((f"{p['pub']} {p['mid']}", why))
            continue
        d = rec["date"]
        pub_day = p["pub"].date() if isinstance(p["pub"], datetime) else p["pub"]
        gap = abs((pub_day - date.fromisoformat(d)).days - 1)
        rank = (-gap, len(rec), p["pub"])
        if d not in by_date or rank > by_meta[d]:
            by_date[d] = rec
            by_meta[d] = rank
    return sorted(by_date.values(), key=lambda r: r["date"]), dropped


def validate_record(rec: dict, today: date) -> tuple[bool, str]:
    try:
        d = date.fromisoformat(rec["date"])
    except Exception:
        return False, "日期格式非法"
    if not (MIN_VALID_DATE <= d <= today):
        return False, f"日期 {d} 越界"
    total = rec.get("total")
    if not isinstance(total, (int, float)) or not (MIN_TOTAL <= total <= MAX_TOTAL):
        return False, f"总量 {total} 越界"
    for k, v in rec.items():
        if k in ("date", "total") or v is None:
            continue
        if not isinstance(v, (int, float)) or not (MIN_LINE <= v <= MAX_LINE):
            return False, f"{k}={v} 越界"
    return True, ""


# ---------------------------------------------------------------- 数据读写


def load_existing() -> list[dict]:
    if not os.path.exists(DATA_JS):
        return []
    with open(DATA_JS, "r", encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"var zzMetroPassengerData = (\[.*?\]);", src, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"现有数据文件损坏：{exc}") from exc
    return data if isinstance(data, list) else []


def load_manual() -> tuple[list[dict], dict[str, dict]]:
    """读取人工补录基线。带 `_override: true` 的条目会强制覆盖数据文件里的错值。"""
    if not os.path.exists(MANUAL_JSON):
        return [], {}
    with open(MANUAL_JSON, "r", encoding="utf-8") as f:
        payload = json.load(f)
    raw = payload.get("records") if isinstance(payload, dict) else payload
    out: list[dict] = []
    forced: dict[str, dict] = {}
    for rec in raw or []:
        clean = {k: v for k, v in rec.items() if not k.startswith("_")}
        ok, why = validate_record(clean, date.today())
        if not ok:
            raise PipelineError(f"人工补录数据非法 {clean.get('date')}：{why}")
        out.append(clean)
        if rec.get("_override"):
            forced[clean["date"]] = clean
    if out:
        log(f"人工补录 {len(out)} 条：{', '.join(r['date'] for r in out)}")
    if forced:
        log(f"  其中强制校正 {len(forced)} 条：{', '.join(sorted(forced))}")
    return out, forced


def render_data_js(records: list[dict]) -> str:
    """一天一行，便于 git diff 与人工核对。"""
    head = (
        "// 郑州地铁客流数据 — 自动生成，请勿手动编辑\n"
        "// 数据来源：微博 @郑州地铁 每日「总客运量」长微博全文（需展开 longtext）\n"
        f"// 记录数：{len(records)} | 日期范围：{records[0]['date']} ~ {records[-1]['date']}\n"
        "// 单位：万人次。每日单列 13 条线路，分线合计与总量自洽。\n"
        "var zzMetroPassengerData = [\n"
    )
    body_lines = []
    for rec in records:
        ordered = {k: rec[k] for k in ORDER if k in rec}
        body_lines.append(json.dumps(ordered, ensure_ascii=False, separators=(",", ":")))
    return head + ",\n".join(body_lines) + "\n];\n"


def write_atomic(records: list[dict]) -> None:
    if not records:
        raise PipelineError("记录集为空，拒绝写入")
    payload = render_data_js(records)
    m = re.search(r"var zzMetroPassengerData = (\[.*?\]);", payload, re.DOTALL)
    if not m:
        raise PipelineError("生成内容自检失败：无法回读数组")
    json.loads(m.group(1))       # 语法自检
    for rec in records:
        if "date" not in rec or "total" not in rec:
            raise PipelineError(f"写入前校验失败：{rec} 缺 date/total")
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
    ap = argparse.ArgumentParser(description="郑州地铁客流数据采集管道（长微博需展开）")
    ap.add_argument("--end", help="截止日期 YYYY-MM-DD（含），默认昨天")
    ap.add_argument("--start", help="起始日期 YYYY-MM-DD（含）")
    ap.add_argument("--backfill-days", type=int, default=0, help="回溯补录天数")
    ap.add_argument("--dry-run", action="store_true", help="只抓取校验，不写文件")
    ap.add_argument("--overwrite", action="store_true", help="冲突时覆盖（默认保留原值）")
    ap.add_argument("--cookie-file", help="微博 Cookie 文本文件路径")
    ap.add_argument("--refresh-cache", action="store_true", help="忽略长文缓存，强制重新展开")
    ap.add_argument("--max-missing", type=int, default=MAX_TOLERATED_MISSING)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    today = date.today()
    end = date.fromisoformat(args.end) if args.end else today - timedelta(days=1)
    if end > today:
        log(f"[错误] 截止日期 {end} 晚于今天 {today}")
        return 1

    # 三个展开接口都要 Cookie，南京那条「无 Cookie 降级到 sina.cn」的路在这里走不通
    cookie = load_cookie(args)
    if not cookie:
        log("[错误] 郑州管道需要微博 Cookie（--cookie-file / WEIBO_COOKIE / .cache/weibo-cookie.txt）")
        gh_output(result="failed", added="0", reason="no-cookie")
        return 1

    existing = load_existing()
    manual, forced = load_manual()
    by_date: dict[str, dict] = {r["date"]: r for r in manual}
    by_date.update({r["date"]: r for r in existing})   # 数据文件里的值优先于人工基线
    by_date.update(forced)                             # 标了 _override 的校正值再压回去
    log(f"现有数据：{len(existing)} 条"
        + (f"，范围 {existing[0]['date']} ~ {existing[-1]['date']}" if existing else "")
        + (f"；含人工补录 {len(manual)} 条" if manual else ""))

    if args.backfill_days > 0:
        start = end - timedelta(days=args.backfill_days - 1)
    elif args.start:
        start = date.fromisoformat(args.start)
    elif existing:
        start = date.fromisoformat(existing[-1]["date"])
    else:
        start = DEFAULT_START
    log(f"目标区间：{start} ~ {end}")

    # 官方是「次日 09:00 发昨日数据」：数据日 D 的微博发布于 D+1。
    # 因此检索窗口必须整体后移一天，否则区间最后一天永远抓不到。
    pub_start, pub_end = start + timedelta(days=1), end + timedelta(days=1)
    log(f"按发布日检索：{pub_start} ~ {pub_end}（对应数据日 {start} ~ {end}）")
    posts = fetch_window_posts(cookie, pub_start, pub_end)
    log(f"共命中候选微博 {len(posts)} 条")
    if not posts:
        log("[错误] 未取到任何微博，Cookie 可能失效或搜索索引变更")
        gh_output(result="failed", added="0", reason="no-posts")
        return 1

    # 关键一步：把列表页的 187 字截断正文逐条展开成全文
    log("展开长微博正文（带缓存，逐条节流）…")
    ok, fail = ensure_full_text(posts, cookie, refresh=args.refresh_cache)
    log(f"  展开成功 {ok} 条，失败 {fail} 条，命中缓存 {len(posts) - ok - fail} 条")
    if fail:
        log(f"  [警告] {fail} 条展开失败，这些日期可能成为缺口")

    recs, dropped = to_records(posts)
    for dst, reason in dropped[:20]:
        log(f"  [跳过] {dst}：{reason}")
    if len(dropped) > 20:
        log(f"  … 另有 {len(dropped) - 20} 条被跳过")
    log(f"解析出有效记录 {len(recs)} 条")

    added, conflicts, same, repaired = [], [], 0, []
    for rec in recs:
        if not (start <= date.fromisoformat(rec["date"]) <= end):
            continue
        old = by_date.get(rec["date"])
        if old is None:
            added.append(rec)
            continue

        # 字段级比对：缺的字段补上（修补），两边都有但值不同才算冲突
        merged = dict(old)
        filled, differs = [], []
        for k in ORDER[1:]:
            nv = rec.get(k)
            if nv is None:
                continue
            ov = merged.get(k)
            if ov is None:
                merged[k] = nv
                filled.append(k)
            elif ov != nv:
                differs.append(k)

        if differs:
            conflicts.append(f"{rec['date']}({','.join(differs[:3])})")
            if args.overwrite:
                by_date[rec["date"]] = rec
                added.append(rec)
        elif filled:
            by_date[rec["date"]] = {k: merged[k] for k in ORDER if k in merged}
            repaired.append(f"{rec['date']}(+{','.join(filled)})")
        else:
            same += 1

    if same:
        log(f"已存在且一致，跳过 {same} 天")
    if repaired:
        log(f"修补 {len(repaired)} 天缺失字段：{repaired[:8]}")
    if conflicts:
        log(f"[警告] {len(conflicts)} 天与仓库不一致"
            + ("（--overwrite 已覆盖）" if args.overwrite else "（保留仓库原值）")
            + f"：{conflicts[:8]}")

    for rec in added:
        by_date[rec["date"]] = rec
    merged = sorted(by_date.values(), key=lambda x: x["date"])
    if not merged:
        log("[错误] 合并后无任何数据，终止且不写入")
        gh_output(result="failed", added="0", reason="empty")
        return 1

    for rec in merged:
        okv, reason = validate_record(rec, today)
        if not okv:
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
        log(f"区间内缺失 {len(missing)}/{span} 天（{ratio:.1%}）"
            f" — {'容忍（多为官方停发或索引边界）' if tolerated else '超限'}")
        log(f"  缺失日期：{missing[:25]}{' …' if len(missing) > 25 else ''}")
        if not tolerated:
            gh_output(result="failed", added=str(len(added)), reason="too-many-missing")
            return 1
    else:
        log(f"区间完整：{start} ~ {end} 共 {span} 天无缺口")

    log(f"合计 {len(merged)} 条，新增 {len(added)} 天，"
        f"范围 {merged[0]['date']} ~ {merged[-1]['date']}")

    # 写盘判据用「最终内容是否与磁盘一致」，不能用「有没有新增数据」——
    # 否则人工补录/字段修补（added 为空）永远不会落盘。
    payload = render_data_js(merged)
    current = ""
    if os.path.exists(DATA_JS):
        with open(DATA_JS, "r", encoding="utf-8") as f:
            current = f.read()
    unchanged = payload == current

    if args.dry_run:
        log("[演练] --dry-run 已启用，不写文件")
        gh_output(result="success", added=str(len(added)), start_date=str(start),
                  end_date=str(end), total=str(len(merged)))
        return 0
    if unchanged:
        log("数据无变化，不写文件")
        gh_output(result="success", added="0", start_date=str(start), end_date=str(end),
                  total=str(len(merged)))
        return 0

    write_atomic(merged)
    log(f"已写入 {DATA_JS}")
    gh_output(result="success", added=str(len(added)), start_date=str(start),
              end_date=str(end), total=str(len(merged)))
    gh_summary("郑州地铁客流采集", [
        f"新增 {len(added)} 天，合计 {len(merged)} 天",
        f"数据区间：`{start}` ~ `{end}`",
        f"区间内缺口：{len(missing)} 天",
        "长微博逐条展开 longtext 后解析，分线合计与总量逐日校验",
    ])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PipelineError as exc:
        log(f"[错误] {exc}")
        gh_output(result="failed", added="0", reason="pipeline-error")
        sys.exit(1)
