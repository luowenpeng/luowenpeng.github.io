#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""南京地铁客流数据采集管道

数据源：微博 @南京地铁（uid=2638276292）每日「#昨日客流#」微博。
与重庆最大的区别——**分线路数值直接写在正文里，纯文本可解析**，
不需要下载配图，也不需要 OCR。整条管道就是「抓 HTML → 正则 → 校验 → 落盘」。

正文样例（实际抓到的两种排版）：
    #昨日客流#南京地铁11月3日客运量340.73，其中1号线64.62，2号线60.75，…（以上单位: 万）
    #昨日客流# 南京地铁11月2日客运量358.66，其中：1号线68.93，…（单位：万）

设计要点（与西安、重庆两条管道保持一致）：
  * 幂等：按日期合并，已存在且数值一致的日期跳过；
  * 原子写：临时文件 → 回读校验 → os.replace，中途失败不留半个文件；
  * 允许缺口：官方停发的日期就是没有，不因为缺一天整批回滚；
  * 线路口径随开通时间变化（6号线、S2号线为后开通），缺报的线路写 null。

用法：
    python scripts/update-nj-metro-data.py --backfill-days 14
    python scripts/update-nj-metro-data.py --start 2026-01-01 --end 2026-11-03
    python scripts/update-nj-metro-data.py --source sina --backfill-days 20
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

UID = "2638276292"                      # 微博 @南京地铁
ACCOUNT = "南京地铁"
TAG = "昨日客流"

# 主接口：微博 PC 端时间线。实测 page 1~60 稳定，更深的分页会限流超时，
# 因此深层失败按「容忍」处理——保留已抓到的部分，不整批失败。
API = f"https://weibo.com/ajax/statuses/mymblog?uid={UID}&page={{page}}&feature=0"
SEARCH_API = ("https://weibo.com/ajax/statuses/searchProfile"
              f"?uid={UID}&q={urllib.parse.quote(TAG)}&page={{page}}")

# 免登录源：sina.cn 媒体聚合页，服务端渲染微博全文，返回最近约 100 条（≈20 天）。
# 周更只补 7 天，完全够用——所以这条管线不配 Cookie 也能长期跑。
SINA_MEDIA = f"https://www.sina.cn/media/{UID}"

DATA_JS = os.path.join("docs", "public", "nj-metro-passenger-data.js")
# 人工补录基线：个别日期官方微博无法通过接口检索到（如 2026 年五一假期），
# 由人工从官方微博页面取得后写入本文件。脚本每次运行都把它作为基线合并，
# 因此从零重建（新克隆 / CI 首次跑）也不会丢这些日期。
MANUAL_JSON = os.path.join("scripts", "nj-metro-manual.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 南京地铁已开通线路（按线路号自然序）。6号线、S2号线为后开通，早期数据里没有。
LINES = [
    ("line1", "1号线"),
    ("line2", "2号线"),
    ("line3", "3号线"),
    ("line4", "4号线"),
    ("line5", "5号线"),
    ("line6", "6号线"),
    ("line7", "7号线"),
    ("line10", "10号线"),
    ("s1", "S1号线"),
    ("s2", "S2号线"),
    ("s3", "S3号线"),
    ("s6", "S6号线"),
    ("s7", "S7号线"),
    ("s8", "S8号线"),
    ("s9", "S9号线"),
]
ORDER = ["date", "total"] + [k for k, _ in LINES]
KEY_OF_LABEL = {lb: k for k, lb in LINES}

# 数值合理区间（万人次）。
# 下限压到 30：春节（2026-02-16 除夕）当日仅 96.5 万，台风停运日（2026-07-12）仅 68.82 万，
# 用 100 当门槛会把这两类真实极值误判成脏数据。
MIN_TOTAL, MAX_TOTAL = 30.0, 900.0
MIN_LINE, MAX_LINE = 0.0, 300.0     # 0 = 当日该线停运（官方写作「S1号线停运」）
MAX_SUM_DIFF = 0.8          # 15 个两位小数累加的舍入容差
MIN_LINES_PER_DAY = 8

MIN_VALID_DATE = date(2024, 1, 1)
DEFAULT_START = date(2026, 1, 1)

MAX_TOLERATED_MISSING = 6
MAX_MISSING_RATIO = 0.12

HTTP_TIMEOUT = 40
HTTP_RETRIES = 3
PAGE_SLEEP = 0.5
MAX_PAGES = 200
MAX_SEARCH_PAGES = 12

# ---------------------------------------------------------------- 正则
#
# 官方正文有三种日期写法，必须都认：
#   「南京地铁11月3日客运量340.73」   完整月日（最常见）
#   「南京地铁16日线网客运量324.04」  只给日，月份靠发布时间反推
#   「【南京地铁运营】12日线网客运量341.97」 同样只给日，且带前缀
# 另有停运日：「其中1号线12.07，…S1号线停运，S2号线停运，…」
DATE_FULL_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")
DATE_DAY_RE = re.compile(r"(?:南京地铁[^\d]{0,10}|】[^\d]{0,6})(\d{1,2})\s*日")
TOTAL_RE = re.compile(r"客运量\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)")
LINE_RE = re.compile(r"(S?\d{1,2})\s*号线\s*[:：]?\s*(停运|([0-9]+(?:\.[0-9]+)?))")


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


def http_get(url: str, *, cookie: str = "", referer: str = "") -> str:
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
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < HTTP_RETRIES:
                time.sleep(1.5 * attempt)
    raise PipelineError(f"GET 失败（{HTTP_RETRIES} 次）：{url} — {last_err}")


def load_cookie(args) -> str:
    if args.cookie_file and os.path.exists(args.cookie_file):
        with open(args.cookie_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    env = os.environ.get("WEIBO_COOKIE", "").strip()
    if env:
        return env
    for cand in (".cache/weibo-cookie.txt", ".cache/nj-cookie.txt"):
        if os.path.exists(cand):
            with open(cand, "r", encoding="utf-8") as f:
                txt = f.read().strip()
            if txt:
                log(f"  使用本地 Cookie 文件：{cand}")
                return txt
    return ""


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").replace("&nbsp;", " ").strip()


# ---------------------------------------------------------------- 正文解析


def infer_date(body: str, pub: date) -> date | None:
    """从正文里推断这条客流对应的运营日。

    优先用完整的「X月Y日」；否则用只给日的「Y日」，按发布时间反推月份
    （客流总是发布日的昨天或前天，跨月/跨年时把候选月份回拨一格再校验）。
    """
    m = DATE_FULL_RE.search(body)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = pub.year
        if month == 12 and pub.month == 1:      # 元旦发上年 12-31 的数据
            year -= 1
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        return d if pub - timedelta(days=3) <= d <= pub else None

    m = DATE_DAY_RE.search(body)
    if not m:
        return None
    day = int(m.group(1))
    for back in (0, 1, 2):
        y, mo = pub.year, pub.month - back
        while mo <= 0:
            mo += 12
            y -= 1
        try:
            cand = date(y, mo, day)
        except ValueError:
            continue
        if pub - timedelta(days=3) <= cand <= pub:
            return cand
    return None


def parse_text(text: str, pub: date) -> tuple[dict | None, str]:
    """把一条微博正文解析成 {"date","total",<lineKey>:...}，失败返回 (None, 原因)。"""
    body = strip_tags(text)
    if TAG not in body and "客运量" not in body:
        return None, "非客流微博"

    mtotal = TOTAL_RE.search(body)
    if not mtotal:
        return None, "缺总量"
    d = infer_date(body, pub)
    if d is None:
        return None, "缺日期"

    total = round(float(mtotal.group(1)), 2)
    if not (MIN_TOTAL <= total <= MAX_TOTAL):
        return None, f"总量 {total} 越界"

    rec: dict = {"date": d.isoformat(), "total": total}
    valid_keys = {k for k, _ in LINES}
    for tag, raw, num in LINE_RE.findall(body):
        key = "s" + tag[1:] if tag.upper().startswith("S") else "line" + tag
        if key not in valid_keys or key in rec:
            continue
        v = 0.0 if (not num or raw == "停运") else round(float(num), 2)
        if MIN_LINE <= v <= MAX_LINE:
            rec[key] = v

    n = len(rec) - 2
    if n < MIN_LINES_PER_DAY:
        return None, f"仅解析到 {n} 条线路"

    present = sum(v for k, v in rec.items() if k not in ("date", "total"))
    diff = abs(present - total)
    if diff > MAX_SUM_DIFF:
        # 不拒收——可能是官方漏报某条线；但要留痕，便于人工核对
        rec["_sum_warn"] = round(diff, 2)
    return rec, ""


def _collect(lst: list[dict], posts: list[dict], seen: set[str]) -> tuple[int, date | None]:
    """从一页微博里挑出客流条目。返回 (本页命中数, 本页最早发布时间)。"""
    hit = 0
    oldest: date | None = None
    for mb in lst:
        try:
            pub = datetime.strptime(mb.get("created_at", ""), "%a %b %d %H:%M:%S %z %Y").date()
        except Exception:
            continue
        oldest = pub if oldest is None or pub < oldest else oldest
        body = strip_tags(mb.get("text", ""))
        # 官方有些日子的微博正文**不带 #昨日客流# 标签**（如 2026 五一三天），
        # 只认标签会永久漏抓，因此「客运量」也放行，交由 parse_text 做实质校验。
        if TAG not in body and "客运量" not in body:
            continue
        mid = str(mb.get("mid") or mb.get("id"))
        if mid in seen:
            continue
        seen.add(mid)
        posts.append({"mid": mid, "pub": pub, "text": body})
        hit += 1
    return hit, oldest


def fetch_posts(cookie: str, start: date) -> list[dict]:
    """翻页抓取时间线（微博 PC 端）。深层分页限流时保留已抓部分。"""
    log("抓取微博时间线 mymblog …")
    posts: list[dict] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        try:
            raw = http_get(API.format(page=page), cookie=cookie,
                           referer=f"https://weibo.com/u/{UID}")
        except PipelineError as exc:
            log(f"  第 {page} 页抓取失败，停止翻页（保留已抓 {len(posts)} 条）：{exc}")
            break
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            log(f"  第 {page} 页非 JSON，停止翻页")
            break
        lst = (payload.get("data") or {}).get("list") or []
        if not lst:
            log(f"  第 {page} 页：无数据，翻页结束")
            break

        hit, oldest = _collect(lst, posts, seen)
        if page % 10 == 0 or page == 1:
            log(f"  第 {page:>3} 页：命中 {hit}，本页最早 {oldest}，累计 {len(posts)}")
        if oldest and oldest < start - timedelta(days=5):
            log(f"  已翻到 {oldest}，早于目标起点，停止翻页")
            break
        time.sleep(PAGE_SLEEP)
    return posts


def fetch_search_posts(cookie: str, start: date) -> list[dict]:
    """关键词搜索源：#昨日客流# 精确检索本账号微博（每页 50 条，约 4 页封顶）。"""
    log(f"抓取微博搜索源 searchProfile（q={TAG}）…")
    posts: list[dict] = []
    seen: set[str] = set()
    for page in range(1, MAX_SEARCH_PAGES + 1):
        try:
            raw = http_get(SEARCH_API.format(page=page), cookie=cookie,
                           referer=f"https://weibo.com/u/{UID}")
            payload = json.loads(raw)
        except (PipelineError, json.JSONDecodeError) as exc:
            log(f"  搜索第 {page} 页不可用：{exc}")
            break
        lst = (payload.get("data") or {}).get("list") or []
        if not lst:
            break
        hit, oldest = _collect(lst, posts, seen)
        log(f"  搜索第 {page} 页：命中 {hit}，本页最早 {oldest}")
        if oldest and oldest < start - timedelta(days=5):
            break
        time.sleep(PAGE_SLEEP)
    return posts


BJ_TZ = timezone(timedelta(hours=8))
WINDOW_DAYS = 30
MAX_WINDOW_PAGES = 6


def epoch_bj(d: date, *, end_of_day: bool = False) -> int:
    """北京时间的 0 点 / 23:59:59 对应的时间戳。

    显式带 +08:00 时区，避免在 UTC 的 CI 机器上算错 8 小时。
    """
    hh, mm, ss = (23, 59, 59) if end_of_day else (0, 0, 0)
    return int(datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=BJ_TZ).timestamp())


def fetch_window_posts(cookie: str, start: date, end: date) -> list[dict]:
    """按时间窗检索（searchProfile + starttime/endtime）。

    这是覆盖最完整的一个源，也是唯一能捞到「无标签微博」的源：
    官方个别日子的正文根本没有 #昨日客流# 标签（2026 五一三天就是如此），
    关键词检索「昨日客流」永远抓不到，所以这里改用「客运量」+ 时间窗逐窗枚举。
    """
    log(f"按时间窗检索 searchProfile（q=客运量，{WINDOW_DAYS} 天一窗）…")
    posts: list[dict] = []
    seen: set[str] = set()
    cur = start
    while cur <= end:
        wend = min(cur + timedelta(days=WINDOW_DAYS - 1), end)
        s_ts, e_ts = epoch_bj(cur), epoch_bj(wend, end_of_day=True)
        got = 0
        for page in range(1, MAX_WINDOW_PAGES + 1):
            url = (f"https://weibo.com/ajax/statuses/searchProfile?uid={UID}"
                   f"&q={urllib.parse.quote('客运量')}&page={page}"
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
            hit, oldest = _collect(lst, posts, seen)
            got += hit
            if oldest and oldest < cur - timedelta(days=3):
                break
            time.sleep(PAGE_SLEEP)
        log(f"  {cur} ~ {wend}：新增 {got} 条（累计 {len(posts)}）")
        cur = wend + timedelta(days=1)
    return posts


def fetch_sina_posts() -> list[dict]:
    """免登录源：sina.cn 媒体页（服务端渲染全文，约最近 100 条）。"""
    log("抓取免登录源 sina.cn …")
    try:
        html = http_get(SINA_MEDIA)
    except PipelineError as exc:
        log(f"  sina 源不可用：{exc}")
        return []
    blocks = re.split(r'<a class="post-link" href="/news/detail/', html)[1:]
    posts: list[dict] = []
    for b in blocks:
        mtime = re.search(r'<div class="time">(\d{4}-\d{2}-\d{2})', b)
        mtxt = re.search(r'<div class="post-text">(.*?)</div>', b, re.DOTALL)
        if not mtime or not mtxt:
            continue
        text = strip_tags(mtxt.group(1))
        if TAG not in text and "客运量" not in text:
            continue
        try:
            pub = date.fromisoformat(mtime.group(1))
        except ValueError:
            continue
        mid = b[:b.find(".html")]
        posts.append({"mid": mid, "pub": pub, "text": text})
    log(f"  列表页命中 {len(posts)} 条「{TAG}」微博")
    return posts


# ---------------------------------------------------------------- 记录合并


def to_records(posts: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    """逐条解析 → 按日期去重（同一天保留线路最全、发布最晚的版本）。"""
    by_date: dict[str, dict] = {}
    dropped: list[tuple[str, str]] = []
    for p in posts:
        rec, why = parse_text(p["text"], p["pub"])
        if rec is None:
            if why != "非客流微博":
                dropped.append((str(p["pub"]), why))
            continue
        rec.pop("_sum_warn", None)
        d = rec["date"]
        old = by_date.get(d)
        if old is None or len(rec) > len(old):
            by_date[d] = rec
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
        content = f.read()
    m = re.search(r"var njMetroPassengerData = (\[.*?\]);", content, re.DOTALL)
    if not m:
        raise PipelineError("数据文件格式错误：未匹配到 var njMetroPassengerData = [...];")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"数据文件 JSON 解析失败：{exc}") from exc


def load_manual() -> list[dict]:
    """读取人工补录基线（scripts/nj-metro-manual.json）。

    只用于官方微博接口检索不到的日期。以 `_` 开头的是元信息字段（如 _source），
    写入数据文件前会被丢弃；每条都要通过 validate_record，防止手填笔误入库。
    """
    if not os.path.exists(MANUAL_JSON):
        return []
    with open(MANUAL_JSON, "r", encoding="utf-8") as f:
        payload = json.load(f)
    raw = payload.get("records") if isinstance(payload, dict) else payload
    out: list[dict] = []
    for rec in raw or []:
        clean = {k: v for k, v in rec.items() if not k.startswith("_")}
        ok, why = validate_record(clean, date.today())
        if not ok:
            raise PipelineError(f"人工补录数据非法 {clean.get('date')}：{why}")
        out.append(clean)
    if out:
        log(f"人工补录 {len(out)} 条：{', '.join(r['date'] for r in out)}")
    return out


def render_data_js(records: list[dict]) -> str:
    """一天一行，便于 git diff 与人工核对。"""
    head = (
        "// 南京地铁客流数据 — 自动生成，请勿手动编辑\n"
        "// 数据来源：微博 @南京地铁 每日「#昨日客流#」正文（纯文本解析，无 OCR）\n"
        f"// 记录数：{len(records)} | 日期范围：{records[0]['date']} ~ {records[-1]['date']}\n"
        "// 单位：万人次。部分线路（6号线、S2号线）为后开通，早期记录中为 null。\n"
        "var njMetroPassengerData = [\n"
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
    m = re.search(r"var njMetroPassengerData = (\[.*?\]);", payload, re.DOTALL)
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
    ap = argparse.ArgumentParser(description="南京地铁客流数据采集管道")
    ap.add_argument("--end", help="截止日期 YYYY-MM-DD（含），默认昨天")
    ap.add_argument("--start", help="起始日期 YYYY-MM-DD（含）")
    ap.add_argument("--backfill-days", type=int, default=0, help="回溯补录天数")
    ap.add_argument("--dry-run", action="store_true", help="只抓取校验，不写文件")
    ap.add_argument("--overwrite", action="store_true", help="冲突时覆盖（默认保留原值）")
    ap.add_argument("--cookie-file", help="微博 Cookie 文本文件路径")
    ap.add_argument("--source", choices=["auto", "sina", "weibo"], default="auto",
                    help="auto=双源合并；sina=免登录（约 20 天）；weibo=需 Cookie 的全量")
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
        log("[错误] --source weibo 需要 Cookie（--cookie-file 或环境变量 WEIBO_COOKIE）")
        return 1

    existing = load_existing()
    manual = load_manual()
    by_date: dict[str, dict] = {r["date"]: r for r in manual}
    by_date.update({r["date"]: r for r in existing})   # 数据文件里的值优先于人工基线
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
    if not cookie:
        log("[提示] 未提供 Cookie，仅覆盖 sina.cn 最近约 20 天；回填历史需 Cookie。")

    posts: list[dict] = []
    if args.source in ("auto", "sina"):
        posts = fetch_sina_posts()
    if args.source in ("auto", "weibo") and cookie:
        extra: list[dict] = []
        try:
            extra = fetch_posts(cookie, start)
        except PipelineError as exc:
            log(f"  [警告] 时间线源不可用：{exc}")
        try:
            extra += fetch_search_posts(cookie, start)
        except PipelineError as exc:
            log(f"  [警告] 搜索源不可用：{exc}")
        try:
            extra += fetch_window_posts(cookie, start, end)
        except PipelineError as exc:
            log(f"  [警告] 时间窗检索不可用：{exc}")
        merged = {p["mid"]: p for p in posts}
        for p in extra:
            merged.setdefault(p["mid"], p)
        posts = sorted(merged.values(), key=lambda p: p["pub"])

    log(f"共命中「{TAG}」微博 {len(posts)} 条")
    if not posts:
        log("[错误] 未取到任何微博，Cookie 可能失效或账号结构变更")
        gh_output(result="failed", added="0", reason="no-posts")
        return 1

    recs, dropped = to_records(posts)
    for dst, reason in dropped[:20]:
        log(f"  [跳过] {dst}：{reason}")
    if len(dropped) > 20:
        log(f"  … 另有 {len(dropped) - 20} 条被跳过")
    log(f"解析出有效记录 {len(recs)} 条")

    added, conflicts, same = [], [], 0
    for rec in recs:
        if not (start <= date.fromisoformat(rec["date"]) <= end):
            continue
        old = by_date.get(rec["date"])
        if old is None:
            added.append(rec)
        elif all(old.get(k) == rec.get(k) for k in ORDER[1:]):
            same += 1
        else:
            conflicts.append(rec["date"])
            if args.overwrite:
                by_date[rec["date"]] = rec
                added.append(rec)

    if same:
        log(f"已存在且一致，跳过 {same} 天")
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
        ok, reason = validate_record(rec, today)
        if not ok:
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
            f" — {'容忍（多为官方停发）' if tolerated else '超限'}")
        log(f"  缺失日期：{missing[:25]}{' …' if len(missing) > 25 else ''}")
        if not tolerated:
            gh_output(result="failed", added=str(len(added)), reason="too-many-missing")
            return 1
    else:
        log(f"区间完整：{start} ~ {end} 共 {span} 天无缺口")

    log(f"合计 {len(merged)} 条，新增 {len(added)} 天，"
        f"范围 {merged[0]['date']} ~ {merged[-1]['date']}")

    # 写盘判据用「最终内容是否与磁盘一致」，而不是「有没有新抓到的数据」——
    # 否则人工补录条目（added 为空）永远不会落盘。
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
    gh_summary("南京地铁客流采集", [
        f"数据区间：`{start}` ~ `{end}`",
        f"新增 {len(added)} 天，库内共 {len(merged)} 条",
        f"全量范围：`{merged[0]['date']}` ~ `{merged[-1]['date']}`",
        f"区间缺口：{len(missing)} 天",
    ])
    return 0


if __name__ == "__main__":
    sys.exit(main())
