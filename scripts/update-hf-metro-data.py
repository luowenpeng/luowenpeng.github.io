#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合肥轨道交通客流数据采集管道。

数据源：微博 @合肥轨道交通（uid=5836845536）每日「客流信息 / 客流播报」微博。
与另外几条管道的差异（这是本脚本存在的理由）：
  * 西安：正文含分线数据；
  * 重庆：分线数据只在配图里，要 OCR；
  * 南京：正文含分线数据，日期写法多变且有官方日期标签笔误；
  * 郑州：全部是长微博，必须逐条展开 longtext；
  * 合肥：历史跨度最长、正文格式更换过三代，且早期单位不是「万人次」：
      - 2018 年「#客流日报#」：先写累计「线网总客运量」，分号后才是当日
        「X日线网客运量为 N 人次」，括号里给 1/2 号线原始乘次；
      - 2019–2022 年「#客流信息#」：多数只给线网总量，单位有「乘次」
        「人次」「万人次」「万乘次」四种；
      - 2023 年起「#客流播报#」：总量 + 逐线数值，线路从 5 条逐步增加到 8 条。
    所以不能用简单的「客运量 + 数字」正则，必须用「日期紧邻线网客运量」
    锁定当日值，并把累计总量、假期汇总、单月新高、跨年纪录全部排除。

正文样例：
    #客流日报# 截至1月9日，线网总客运量为4681.3628万人次（…）；
    1月9日线网客运量为369318人次（1号线177591人次，2号线191727人次）…
    #客流信息# 12月09日线网客运量为467828乘次，线网进站客流前三车站…
    #客流播报# 11月4日线网客运量206.03万人次，其中：1号线25.15万人次，…

接口实测结论（2026-11-05）：
  * 检索：`weibo.com/ajax/statuses/searchProfile` + `q=客运量`
    + `starttime`/`endtime`（**北京时间** epoch）。参数名必须是
    `starttime`/`endtime`，写成 `start_time`/`timescope` 会被静默忽略。
  * 该账号的 searchProfile 时间窗可回溯到 2018-01 前后，但 2018-05~2019-09、
    2022-11~2023-02 存在官方停发或索引缺口，不是脚本漏抓。
  * 每日客流微博基本都是短文本；少量节假日汇总长微博会被正则拒收。
    对 `isLongText` 仍保留 longtext 展开降级，避免个别日期被折叠截断。
  * `searchProfile` 强制需要登录态 Cookie（免登录返回 `ok=-100`），
    因此日常周更走本机 `scripts/local-hf-metro-sync.py`，读取
    `.cache/weibo-cookie.txt`，凭据不上传 GitHub Secret。

设计要点（沿用前几条管道已验证的做法）：
  * 幂等：按日期合并，一致则跳过；字段级修补，只有真值不同才算冲突；
  * 原子写：临时文件 → 回读校验 → os.replace；
  * 写盘判据用「最终内容是否与磁盘一致」，不能用「有没有新增数据」；
  * 同日多条按「发布日离数据日+1 最近」优先；
  * 允许缺口：官方停发/索引缺口的日期就是没有，不因此整批回滚；
  * 有逐线数值的日期做「分线合计 == 总量」硬校验，早期总量-only 日期放行。

用法（日常周更走本机包装脚本，下面这些用于诊断/回填）：
    python scripts/local-hf-metro-sync.py
    python scripts/update-hf-metro-data.py --backfill-days 14 --dry-run
    python scripts/update-hf-metro-data.py --start 2018-01-01 --end 2026-11-04 --max-missing 800
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

try:  # Windows 控制台中文输出兜底
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------- 配置常量

UID = "5836845536"                      # 微博 @合肥轨道交通
ACCOUNT = "合肥轨道交通"
TAG = "客运量"

SEARCH_API = ("https://weibo.com/ajax/statuses/searchProfile"
              f"?uid={UID}&q={{q}}&page={{page}}")
LONGTEXT_API = "https://weibo.com/ajax/statuses/longtext?id={mid}"
M_EXTEND_API = "https://m.weibo.cn/statuses/extend?id={id}"
M_SHOW_API = "https://m.weibo.cn/statuses/show?id={id}"

DATA_JS = os.path.join("docs", "public", "hf-metro-passenger-data.js")
MANUAL_JSON = os.path.join("scripts", "hf-metro-manual.json")
CACHE_JSON = os.path.join(".cache", "hf-longtext.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 合肥轨道已开通并单列的线路（近一年内格式固定为 1~8 号线）。
LINES = [
    ("line1", "1号线"),
    ("line2", "2号线"),
    ("line3", "3号线"),
    ("line4", "4号线"),
    ("line5", "5号线"),
    ("line6", "6号线"),
    ("line7", "7号线"),
    ("line8", "8号线"),
]
ORDER = ["date", "total"] + [k for k, _ in LINES]
KEY_OF_LABEL = {lb: k for k, lb in LINES}

# 数值合理区间（万人次）。2018 年日均约 30~40 万，跨年夜最高约 361 万。
MIN_TOTAL, MAX_TOTAL = 10.0, 600.0
MIN_LINE, MAX_LINE = 0.0, 300.0
MAX_SUM_DIFF = 0.8          # 8 个两位小数累加的舍入容差
MIN_LINES_WHEN_PRESENT = 2  # 一旦出现逐线数值，至少应有 2 条（2018/2019 的 1、2 号线）

MIN_VALID_DATE = date(2016, 1, 1)
DEFAULT_START = date(2018, 1, 1)   # searchProfile 可检索到的最早连续区间

MAX_TOLERATED_MISSING = 900  # 全量回填时历史缺口较多；周更由本机脚本收紧到 6
MAX_MISSING_RATIO = 0.12

HTTP_TIMEOUT = 25
HTTP_RETRIES = 3
PAGE_SLEEP = 0.25
EXPAND_SLEEP = 0.12
WINDOW_DAYS = 30
MAX_WINDOW_PAGES = 6
CACHE_FLUSH_EVERY = 25

BJ_TZ = timezone(timedelta(hours=8))

# ---------------------------------------------------------------- 正则
#
# 关键：只认「日期紧邻线网客运量」的当日值。
#   - (?<!截至) 排除 2018 年「截至X月X日，线网总客运量…」的累计句；
#   - (?!总)    再排除「线网总客运量 / 线网累计客运量」；
#   - 月份可省略，兼容 2018 年的「7日线网客运量为…」；
#   - 单位含早期「乘次 / 人次 / 万乘次」与近期「万人次 / 万次 / 万人」。
DAILY_RE = re.compile(
    r"(?<!截至)(?:(\d{4})\s*年\s*)?(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*日[，,、]?\s*"
    r"(?:合肥轨道交通|合肥轨道)?线网(?:客运总量|客运量|运送乘客)\s*(?:为|达|约)?\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*(万人次|万乘次|万次|万人|乘次|人次|人)?"
)
# 官方偶有「2号30.98万人次」「3号33.87万人次」漏写「线」，也有
# 「1号线客运量32.6856万人次」的写法，所以「线」「客运量」都做成可选。
LINE_RE = re.compile(
    r"(\d{1,2})\s*号\s*线?\s*(?:客运量)?\s*(?:的|为)?\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*(万人次|万乘次|万次|万人|乘次|人次|人)?"
)

WAN_UNITS = {"万人次", "万乘次", "万次", "万人"}
RAW_UNITS = {"乘次", "人次", "人"}

# 这些不是每日逐日数据：假期汇总、单月/季度/春运总结、跨年纪录等。
NON_DAILY_MARKERS = (
    "累计客运量", "日均客运量", "跨年夜", "春运", "单月", "第一季度",
    "单日总客运量", "再创单月新高", "假期6天", "假期（", "假期线网",
)


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
                time.sleep(1.0 * attempt)
    raise PipelineError(f"GET 失败（{HTTP_RETRIES} 次）：{url} — {last_err}")


def load_cookie(args) -> str:
    if args.cookie_file and os.path.exists(args.cookie_file):
        with open(args.cookie_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    env = os.environ.get("WEIBO_COOKIE", "").strip()
    if env:
        return env
    for cand in (".cache/weibo-cookie.txt", ".cache/hf-cookie.txt"):
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
    """北京时间的 0 点 / 23:59:59 对应的时间戳。"""
    hh, mm, ss = (23, 59, 59) if end_of_day else (0, 0, 0)
    return int(datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=BJ_TZ).timestamp())


def to_wan(num: str, unit: str | None) -> float:
    """统一换算为万人次。

    早期「乘次 / 人次 / 人」是原始计数（几十万），需除以 10000；
    正文偶尔漏单位，也按原始计数处理。若原始单位后面跟的是小数（如
    「3号线15.26人次」），显然是官方漏写「万」，按万人次收录而不除以 10000。
    """
    value = float(num)
    if unit is None or unit in RAW_UNITS:
        if value >= 1000:
            value /= 10000.0
    elif unit not in WAN_UNITS:
        raise ValueError(f"未知单位：{unit}")
    return round(value, 2)


# ---------------------------------------------------------------- 长微博降级


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
    """展开一条长微博，返回全文；三个接口依次降级。"""
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
                if txt and "客运量" in txt:
                    return txt
        except (PipelineError, json.JSONDecodeError, ValueError):
            continue
    return None


def ensure_full_text(posts: list[dict], cookie: str, refresh: bool = False) -> tuple[int, int, int, int]:
    """只对折叠候选做展开；短文本直接入缓存。返回 (成功, 失败, 命中缓存, 短文本)。"""
    cache = load_cache()
    ok = fail = hit = short = 0
    dirty = 0
    for i, p in enumerate(posts, 1):
        key = str(p.get("blogid") or p.get("mid") or p.get("id") or "")
        if not refresh and key and key in cache:
            p["text"] = cache[key]
            hit += 1
            continue
        if not p.get("is_long"):
            cache[key] = p["text"]
            short += 1
            dirty += 1
        else:
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
    return ok, fail, hit, short


# ---------------------------------------------------------------- 正文解析


def infer_daily_date(m: re.Match, pub: date) -> date | None:
    """从 DAILY_RE 的捕获组推断运营日。"""
    year_s, month_s, day_s = m.group(1), m.group(2), m.group(3)
    day = int(day_s)

    if year_s:
        year, month = int(year_s), int(month_s or pub.month)
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        return d if pub - timedelta(days=4) <= d <= pub else None

    if month_s:
        month = int(month_s)
        year = pub.year
        if month == 12 and pub.month == 1:      # 元旦发上年 12 月的数据
            year -= 1
        elif month == 1 and pub.month == 12:    # 跨年补发，极少见
            year += 1
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        return d if pub - timedelta(days=4) <= d <= pub else None

    # 只给「7日」：按发布日往回推月份（最多 3 个月，但要求 4 天内发布）
    for back in (0, 1, 2):
        y, mo = pub.year, pub.month - back
        while mo <= 0:
            mo += 12
            y -= 1
        try:
            cand = date(y, mo, day)
        except ValueError:
            continue
        if pub - timedelta(days=4) <= cand <= pub:
            return cand
    return None


def parse_text(text: str, pub: date) -> tuple[dict | None, str]:
    """把一条微博正文解析成 {"date","total",<lineKey>:...}，失败返回 (None, 原因)。"""
    body = strip_tags(text)
    if "展开" in body[-12:]:
        return None, "正文未展开"
    if any(marker in body for marker in NON_DAILY_MARKERS):
        return None, "非每日"

    m = DAILY_RE.search(body)
    if not m:
        return None, "缺每日总量"
    d = infer_daily_date(m, pub)
    if d is None:
        return None, "日期与发布日不符"

    total = to_wan(m.group(4), m.group(5))
    if not (MIN_TOTAL <= total <= MAX_TOTAL):
        return None, f"总量 {total} 越界"

    rec: dict = {"date": d.isoformat(), "total": total}
    # 只解析当日总量之后的分线数值：
    #   - 2018 年累计句在当日总量之前，本就已跳过；
    #   - 2020 年前后偶有「截止X月X日，线网总客运量…」追加在当日数据后面，
    #     其中 1/2/3 号线是累计值（动辄上万），必须截掉，否则会污染分线解析。
    segment = body[m.end():]
    for cut in ("截止", "截至", "线网总客运量", "累计客运量", "日均客运量"):
        idx = segment.find(cut)
        if idx != -1:
            segment = segment[:idx]
    for num_label, num, unit in LINE_RE.findall(segment):
        label = f"{int(num_label)}号线"
        key = KEY_OF_LABEL.get(label)
        if key is None or key in rec:
            continue
        v = to_wan(num, unit)
        if MIN_LINE <= v <= MAX_LINE:
            rec[key] = v

    n = len(rec) - 2
    if n and n < MIN_LINES_WHEN_PRESENT:
        return None, f"仅解析到 {n} 条线路"
    if n == 0 and "其中" in segment:
        return None, "缺分线数值"

    if n >= MIN_LINES_WHEN_PRESENT:
        present = sum(v for k, v in rec.items() if k not in ("date", "total"))
        diff = abs(present - total)
        if diff > MAX_SUM_DIFF:
            # 官方偶有加错或某一线漏写「万」的笔误。按官方原值收录、留痕，
            # 不自行平衡，也不把这种日期丢成缺口（南京管道已验证过这个取舍）。
            rec["_sum_warn"] = round(diff, 2)
    return rec, ""


# ---------------------------------------------------------------- 数据抓取


def _collect(lst: list[dict], posts: list[dict], seen: set[str]) -> int:
    """从一页搜索结果里挑出候选客流微博。"""
    hit = 0
    for mb in lst:
        try:
            pub = datetime.strptime(mb.get("created_at", ""), "%a %b %d %H:%M:%S %z %Y").date()
        except Exception:
            continue
        raw = strip_tags(mb.get("text_raw") or mb.get("text") or "")
        if "客运量" not in raw and "客流" not in raw:
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
    """按 30 天一窗检索 searchProfile（q=客运量）。"""
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
            if payload.get("ok") not in (1, None):
                log(f"  {cur}~{wend} 第 {page} 页返回 ok={payload.get('ok')}，停止本窗")
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


def to_records(posts: list[dict]) -> tuple[list[dict], list[tuple[str, str]], list[str]]:
    """逐条解析 → 按日期去重。同日多条按「发布日离数据日+1 最近」优先。"""
    by_date: dict[str, dict] = {}
    by_meta: dict[str, tuple[int, int, object]] = {}
    dropped: list[tuple[str, str]] = []
    sum_warns: list[str] = []
    for p in posts:
        rec, why = parse_text(p["text"], p["pub"])
        if rec is None:
            if why != "非每日":
                dropped.append((f"{p['pub']} {p['mid']}", why))
            continue
        warn = rec.pop("_sum_warn", None)
        d = rec["date"]
        if warn is not None:
            sum_warns.append(f"{d}(差{warn:.2f})")
        pub_day = p["pub"].date() if isinstance(p["pub"], datetime) else p["pub"]
        gap = abs((pub_day - date.fromisoformat(d)).days - 1)
        rank = (-gap, len(rec), p["pub"])
        if d not in by_date or rank > by_meta[d]:
            by_date[d] = rec
            by_meta[d] = rank
    return sorted(by_date.values(), key=lambda r: r["date"]), dropped, sorted(set(sum_warns))


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
        if k not in ORDER:
            return False, f"未知字段 {k}"
        if not isinstance(v, (int, float)) or not (MIN_LINE <= v <= MAX_LINE):
            return False, f"{k}={v} 越界"
    return True, ""


# ---------------------------------------------------------------- 数据读写


def load_existing() -> list[dict]:
    if not os.path.exists(DATA_JS):
        return []
    with open(DATA_JS, "r", encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"var hfMetroPassengerData = (\[.*?\]);", src, re.DOTALL)
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
        "// 合肥轨道交通客流数据 — 自动生成，请勿手动编辑\n"
        "// 数据来源：微博 @合肥轨道交通 每日「客流信息 / 客流播报」正文\n"
        f"// 记录数：{len(records)} | 日期范围：{records[0]['date']} ~ {records[-1]['date']}\n"
        "// 单位：万人次。2018–2022 多为线网总量，2023 年起含逐线数据；分线缺失留空。\n"
        "var hfMetroPassengerData = [\n"
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
    m = re.search(r"var hfMetroPassengerData = (\[.*?\]);", payload, re.DOTALL)
    if not m:
        raise PipelineError("生成内容自检失败：无法回读数组")
    json.loads(m.group(1))
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
    ap = argparse.ArgumentParser(description="合肥轨道交通客流数据采集管道")
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

    cookie = load_cookie(args)
    if not cookie:
        log("[错误] 合肥管道需要微博 Cookie（--cookie-file / WEIBO_COOKIE / .cache/weibo-cookie.txt）")
        gh_output(result="failed", added="0", reason="no-cookie")
        return 1

    existing = load_existing()
    manual, forced = load_manual()
    by_date: dict[str, dict] = {r["date"]: r for r in manual}
    by_date.update({r["date"]: r for r in existing})
    by_date.update(forced)
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

    # 官方是「次日 08:00~09:00 发昨日数据」：数据日 D 的微博发布于 D+1。
    pub_start, pub_end = start + timedelta(days=1), end + timedelta(days=1)
    log(f"按发布日检索：{pub_start} ~ {pub_end}（对应数据日 {start} ~ {end}）")
    posts = fetch_window_posts(cookie, pub_start, pub_end)
    log(f"共命中候选微博 {len(posts)} 条")
    if not posts:
        log("[错误] 未取到任何微博，Cookie 可能失效或搜索索引变更")
        gh_output(result="failed", added="0", reason="no-posts")
        return 1

    log("处理折叠候选（带缓存，逐条节流）…")
    ok, fail, hit, short = ensure_full_text(posts, cookie, refresh=args.refresh_cache)
    log(f"  展开成功 {ok} 条，失败 {fail} 条，命中缓存 {hit} 条，短文本 {short} 条")
    if fail:
        log(f"  [警告] {fail} 条展开失败，这些日期可能成为缺口")

    recs, dropped, sum_warns = to_records(posts)
    for dst, reason in dropped[:25]:
        log(f"  [跳过] {dst}：{reason}")
    if len(dropped) > 25:
        log(f"  … 另有 {len(dropped) - 25} 条被跳过")
    log(f"解析出有效记录 {len(recs)} 条")
    if sum_warns:
        suffix = " …" if len(sum_warns) > 8 else ""
        log(f"[提示] {len(sum_warns)} 天官方分线合计与总量不一致，按官方原值收录：{sum_warns[:8]}{suffix}")

    added, conflicts, same, repaired = [], [], 0, []
    for rec in recs:
        if not (start <= date.fromisoformat(rec["date"]) <= end):
            continue
        old = by_date.get(rec["date"])
        if old is None:
            added.append(rec)
            continue

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
            f" — {'容忍（官方停发 / 索引缺口）' if tolerated else '超限'}")
        log(f"  缺失日期：{missing[:25]}{' …' if len(missing) > 25 else ''}")
        if not tolerated:
            gh_output(result="failed", added=str(len(added)), reason="too-many-missing")
            return 1
    else:
        log(f"区间完整：{start} ~ {end} 共 {span} 天无缺口")

    log(f"合计 {len(merged)} 条，新增 {len(added)} 天，"
        f"范围 {merged[0]['date']} ~ {merged[-1]['date']}")

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
        gh_output(result="success", added="0", start_date=str(start),
                  end_date=str(end), total=str(len(merged)))
        return 0

    write_atomic(merged)
    log(f"已写入 {DATA_JS}")
    gh_output(result="success", added=str(len(added)), start_date=str(start),
              end_date=str(end), total=str(len(merged)))
    gh_summary("合肥轨道交通客流采集", [
        f"新增 {len(added)} 天，合计 {len(merged)} 天",
        f"数据区间：`{start}` ~ `{end}`",
        f"区间内缺口：{len(missing)} 天",
        f"官方分线合计不一致：{len(sum_warns)} 天（按原值收录）",
        "按「日期紧邻线网客运量」锁定当日值，逐日校验分线合计与总量",
    ])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PipelineError as exc:
        log(f"[错误] {exc}")
        gh_output(result="failed", added="0", reason="pipeline-error")
        sys.exit(1)
