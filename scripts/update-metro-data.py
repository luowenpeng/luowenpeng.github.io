#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""西安地铁客流数据 — 增量采集管道（云端定时版）

数据源
------
主源  sina.cn 媒体聚合页（服务端渲染微博全文，免登录、免 Cookie）
      https://www.sina.cn/media/2372208183
      注意：该页面**不支持翻页**，只返回最近约 47 条客流微博
      （跨度约 49 天）。实测 2026-10-19 抓取得到 2026-08-30 ~ 2026-10-17。
      因此定时执行间隔必须 < 45 天，否则窗口外的日期将永久缺失。

增强源 微博移动端 API（需 Cookie，可选）
      配置环境变量 WEIBO_COOKIE 后启用，支持翻页，用于补齐
      主源窗口之外的历史数据。未配置时自动跳过，不影响主流程。

设计约束
--------
1. 幂等：以 date 为主键合并，**已存在的日期不覆盖**（除非 --overwrite）。
   同一份输入重复执行，产出的数据文件字节级一致。
2. 不写脏数据：每条记录须通过全字段校验；单条不合规则丢弃该条，
   其余记录照常入库。整批异常（源不可达 / 0 条有效 / 缺口过大）
   则以退出码 1 失败，**不修改任何文件**。
3. 容忍停发：官方偶发停发客流微博（实证 2026-09-15、2026-09-22），
   缺口在阈值内视为正常；超过阈值才判定为采集异常。
4. 原子写：先写临时文件并回读校验，成功后 os.replace 原子替换。

用法
----
  python scripts/update-metro-data.py
      # 增量补录到「昨天」（默认），已存在的日期跳过
  python scripts/update-metro-data.py --dry-run
      # 只抓取并校验，不写文件
  python scripts/update-metro-data.py --backfill-days 45
      # 补跑最近 45 天范围内缺失的日期（历史回填）
  python scripts/update-metro-data.py --end 2026-10-17
      # 指定截止日期（含）

产出
----
  docs/public/metro-passenger-data.js   数据文件（博客栏目页直接引用）
  README.md                             同步更新「N 天全网客流」天数

退出码
------
  0  成功（含「无新数据」）
  1  失败：数据源不可达 / 无有效记录 / 缺口超限 / 写入校验失败
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

try:  # pragma: no cover - Windows 控制台中文输出兜底
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------- 配置常量

SOURCE_URL = "https://www.sina.cn/media/2372208183"
WEIBO_API = (
    "https://m.weibo.cn/api/container/getIndex"
    "?type=uid&value=2253771810&containerid=1076032253771810&page={page}"
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DATA_JS = os.path.join("docs", "public", "metro-passenger-data.js")
README_MD = "README.md"

# 12 条地铁线 + 西户线（"8号(环)线"归为 line8，来源口径保持一致）
LINES = ["1", "2", "3", "4", "5", "6", "8", "9", "10", "14", "15", "16"]
ORDER = ["date", "total"] + [f"line{n}" for n in LINES] + ["linexihu"]

# 业务合理性阈值（单位：万人次）
MIN_TOTAL, MAX_TOTAL = 50.0, 1200.0
MIN_LINE, MAX_LINE = 0.05, 400.0
MAX_SUM_DIFF = 0.2          # 分线和与 total 的容差（沿用既有口径）
MIN_VALID_DATE = date(2020, 1, 1)

# 缺口容忍策略：绝对天数与占比满足其一即放行
MAX_TOLERATED_MISSING = 3
MAX_MISSING_RATIO = 0.10

HTTP_TIMEOUT = 25
HTTP_RETRIES = 3


class PipelineError(Exception):
    """不可恢复的管道错误 → 退出码 1，不落盘任何脏数据。"""


class SourceUnavailable(PipelineError):
    pass


# ---------------------------------------------------------------- 通用工具


def log(msg: str = "") -> None:
    print(msg, flush=True)


def gh_output(**kv) -> None:
    """向 GitHub Actions 输出变量（本地运行时静默跳过）。"""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in kv.items():
            f.write(f"{k}={v}\n")


def gh_summary(title: str, lines: list[str]) -> None:
    """追加 Markdown 片段到 GitHub Actions 作业摘要。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"### {title}\n\n")
        for ln in lines:
            f.write(f"- {ln}\n")
        f.write("\n")


def http_get(url: str, *, cookie: str | None = None) -> str:
    """带指数退避重试的 GET，返回解码后的文本。"""
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        headers["Cookie"] = cookie
    last_err: Exception | None = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read()
            for enc in ("utf-8", "gb18030"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - 网络异常统一收敛
            last_err = exc
            if attempt < HTTP_RETRIES:
                wait = 2 ** (attempt - 1)
                log(f"  [重试 {attempt}/{HTTP_RETRIES - 1}] {exc} — {wait}s 后重试")
                time.sleep(wait)
    raise SourceUnavailable(f"数据源不可达 {url}: {last_err}")


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


# ---------------------------------------------------------------- 字段校验


def validate_record(rec: dict, today: date) -> tuple[bool, str]:
    """单条记录全字段校验。返回 (是否合规, 原因)。"""
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
        return False, f"total {total} 超出合理区间 [{MIN_TOTAL},{MAX_TOTAL}]"

    for n in LINES:
        v = rec.get(f"line{n}")
        if v is None:
            return False, f"缺 line{n}"
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return False, f"line{n} 非数值"
        if not (MIN_LINE <= v <= MAX_LINE):
            return False, f"line{n}={v} 超出合理区间"

    xh = rec.get("linexihu")
    if xh is None or not isinstance(xh, (int, float)) or isinstance(xh, bool):
        return False, "linexihu 非数值"
    if not (MIN_LINE <= xh <= MAX_LINE):
        return False, f"linexihu={xh} 超出合理区间"

    ssum = sum(rec[f"line{n}"] for n in LINES) + rec["linexihu"]
    if abs(ssum - total) > MAX_SUM_DIFF:
        return False, f"分线和 {ssum:.1f} 与 total {total} 偏差 > {MAX_SUM_DIFF}"
    return True, ""


# ---------------------------------------------------------------- 数据源解析


def parse_sina_html(html: str) -> tuple[list[dict], list[tuple[str, str]]]:
    """解析 sina.cn 页面。返回 (合规候选记录, 被丢弃项的(日期,原因))。"""
    times = [(m.start(), m.group(1)) for m in re.finditer(r'<div class="time">(\d{4}-\d{2}-\d{2})', html)]
    texts = [(m.start(), m.group(1)) for m in re.finditer(r'<div class="post-text">(.*?)</div>', html, re.DOTALL)]
    if not times or not texts:
        raise PipelineError("页面结构异常：未找到 time / post-text 节点，数据源可能改版")

    candidates: list[dict] = []
    dropped: list[tuple[str, str]] = []
    today = date.today()

    for tpos, tval in times:
        cands = [(pos, txt) for pos, txt in texts if pos > tpos]
        if not cands:
            continue
        _, raw = min(cands, key=lambda x: x[0])
        body = strip_tags(raw)
        if "客流数据" not in body or "线网客流" not in body:
            continue
        try:
            pub = date.fromisoformat(tval)
        except ValueError:
            dropped.append((tval, "发布日期非法"))
            continue

        md = re.search(r"(\d{1,2})月(\d{1,2})日", body)
        mt = re.search(r"线网客流([\d.]+)万人次", body)
        if not md or not mt:
            dropped.append((tval, "未匹配到日期或线网客流"))
            continue

        month, day = int(md.group(1)), int(md.group(2))
        try:
            d = date(pub.year, month, day)
        except ValueError:
            dropped.append((tval, f"日期非法 {month}月{day}日"))
            continue
        if d > pub:  # 跨年修正：12 月发次年 1 月的数据
            d = date(pub.year - 1, month, day)

        rec = {"date": d.isoformat(), "total": float(mt.group(1))}
        for ln in LINES:
            ml = re.search(rf"{ln}号(?:\(环\))?线([\d.]+)万人次", body)
            rec[f"line{ln}"] = round(float(ml.group(1)), 1) if ml else None
        # 西户线：优先取原文；缺失时用差值回填（已验证与原文一致）
        mx = re.search(r"西户线([\d.]+)万人次", body)
        if mx:
            rec["linexihu"] = round(float(mx.group(1)), 1)
        else:
            base = sum(v for k, v in rec.items() if k.startswith("line") and v is not None)
            rec["linexihu"] = round(rec["total"] - base, 1)

        ok, reason = validate_record(rec, today)
        (candidates if ok else dropped).append(rec if ok else (rec["date"], reason))

    # 同一天可能被多条微博提及 → 取校验通过的第一条，保持幂等
    best: dict[str, dict] = {}
    for rec in candidates:
        best.setdefault(rec["date"], rec)
    return sorted(best.values(), key=lambda x: x["date"]), dropped


def fetch_sina() -> tuple[list[dict], list[tuple[str, str]]]:
    log(f"抓取主源 sina.cn …")
    html = http_get(SOURCE_URL)
    log(f"  页面长度 {len(html):,} 字符")
    recs, dropped = parse_sina_html(html)
    log(f"  解析到 {len(recs)} 条有效记录" + (f"（{recs[0]['date']} ~ {recs[-1]['date']}）" if recs else ""))
    return recs, dropped


def fetch_weibo_api(cookie: str, pages: int = 3) -> list[dict]:
    """可选增强源：微博移动端 API，支持翻页补齐主源窗口外的历史。

    未经充分实测验证，仅在主源覆盖不足且显式配置 Cookie 时启用。
    """
    log(f"抓取增强源 m.weibo.cn（{pages} 页）…")
    out: list[dict] = []
    today = date.today()
    for page in range(1, pages + 1):
        try:
            raw = http_get(WEIBO_API.format(page=page), cookie=cookie)
            payload = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            log(f"  第 {page} 页获取失败：{exc}")
            break
        cards = payload.get("data", {}).get("cards", []) if payload.get("ok") else []
        if not cards:
            break
        hit = 0
        for card in cards:
            mb = card.get("mblog") or {}
            body = strip_tags(mb.get("text", ""))
            if "客流数据" not in body or "线网客流" not in body:
                continue
            created = mb.get("created_at", "")
            try:
                pub = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y").date()
            except Exception:
                pub = today
            md = re.search(r"(\d{1,2})月(\d{1,2})日", body)
            mt = re.search(r"线网客流([\d.]+)万人次", body)
            if not (md and mt):
                continue
            try:
                d = date(pub.year, int(md.group(1)), int(md.group(2)))
            except ValueError:
                continue
            if d > pub:
                d = date(pub.year - 1, int(md.group(1)), int(md.group(2)))
            rec = {"date": d.isoformat(), "total": float(mt.group(1))}
            for ln in LINES:
                ml = re.search(rf"{ln}号(?:\(环\))?线([\d.]+)万人次", body)
                rec[f"line{ln}"] = round(float(ml.group(1)), 1) if ml else None
            mx = re.search(r"西户线([\d.]+)万人次", body)
            if mx:
                rec["linexihu"] = round(float(mx.group(1)), 1)
            else:
                base = sum(v for k, v in rec.items() if k.startswith("line") and v is not None)
                rec["linexihu"] = round(rec["total"] - base, 1)
            ok, _ = validate_record(rec, today)
            if ok:
                out.append(rec)
                hit += 1
        log(f"  第 {page} 页：{hit} 条有效")
        time.sleep(0.5)
    return out


# ---------------------------------------------------------------- 数据读写


def load_existing() -> list[dict]:
    if not os.path.exists(DATA_JS):
        raise PipelineError(f"数据文件不存在：{DATA_JS}")
    with open(DATA_JS, "r", encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"var metroPassengerData = (\[.*\]);", content, re.DOTALL)
    if not m:
        raise PipelineError("数据文件格式错误：未匹配到 var metroPassengerData = [...];")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"数据文件 JSON 解析失败：{exc}") from exc


def render_data_js(records: list[dict]) -> str:
    return (
        "// 西安地铁客流数据 — 自动生成，请勿手动编辑\n"
        "// 数据来源：微博 @西安地铁运营分公司\n"
        f"// 记录数：{len(records)} | 日期范围：{records[0]['date']} ~ {records[-1]['date']}\n"
        "var metroPassengerData = " + json.dumps(records, ensure_ascii=False, indent=2) + ";\n"
    )


def write_atomic(records: list[dict]) -> None:
    """写临时文件 → 回读校验 → 原子替换。任一步失败均不影响原文件。"""
    payload = render_data_js(records)
    if not records:
        raise PipelineError("记录集为空，拒绝写入")
    json.loads(re.search(r"var metroPassengerData = (\[.*\]);", payload, re.DOTALL).group(1))
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


def sync_readme(count: int) -> bool:
    """同步 README 中的天数，返回是否发生变更。幂等。"""
    if not os.path.exists(README_MD):
        log(f"  [警告] 未找到 {README_MD}，跳过年同步")
        return False
    with open(README_MD, "r", encoding="utf-8") as f:
        content = f.read()
    new_content, n = re.subn(r"— \d+ 天全网客流", f"— {count} 天全网客流", content)
    if n == 0:
        log("  [警告] README 未匹配到「N 天全网客流」句式，跳过")
        return False
    if new_content == content:
        return False
    tmp = README_MD + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_content)
    os.replace(tmp, README_MD)
    return True


# ---------------------------------------------------------------- 主流程


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="西安地铁客流数据增量采集管道")
    ap.add_argument("--end", help="截止日期 YYYY-MM-DD（含），默认昨天")
    ap.add_argument("--backfill-days", type=int, default=0,
                    help="回溯补录天数：把该窗口内缺失的日期一并补齐（默认 0 = 仅追尾）")
    ap.add_argument("--dry-run", action="store_true", help="只抓取校验，不写文件")
    ap.add_argument("--overwrite", action="store_true",
                    help="已存在日期若与新值冲突则覆盖（默认保留原值并告警）")
    ap.add_argument("--max-missing", type=int, default=MAX_TOLERATED_MISSING,
                    help=f"容忍的最大缺失天数（默认 {MAX_TOLERATED_MISSING}）")
    ap.add_argument("--source-only", action="store_true",
                    help="忽略增强源，只用 sina.cn 主源")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    today = date.today()
    end = date.fromisoformat(args.end) if args.end else today - timedelta(days=1)
    if end > today:
        log(f"[错误] 截止日期 {end} 晚于今天 {today}")
        return 1

    existing = load_existing()
    by_date = {r["date"]: r for r in existing}
    log(f"现有数据：{len(existing)} 条，范围 {existing[0]['date']} ~ {existing[-1]['date']}")
    log(f"目标截止：{end}")

    # 采集：主源 + 可选增强源
    recs, dropped = fetch_sina()
    cookie = os.environ.get("WEIBO_COOKIE", "").strip()
    if cookie and not args.source_only and recs:
        try:
            extra = fetch_weibo_api(cookie)
            merged_src = {r["date"]: r for r in recs}
            for r in extra:
                merged_src.setdefault(r["date"], r)
            recs = sorted(merged_src.values(), key=lambda x: x["date"])
            log(f"  主源+增强源合计 {len(recs)} 条")
        except Exception as exc:  # noqa: BLE001 - 增强源失败不阻断主流程
            log(f"  [警告] 增强源不可用：{exc}")

    for dst, reason in dropped:
        log(f"  [丢弃] {dst}：{reason}")

    if not recs:
        log("[错误] 未解析到任何有效记录 — 数据源可能改版或结构变更，终止且不写入")
        gh_output(result="failed", added="0", reason="no-valid-records")
        return 1

    # 决定本次需要尝试写入的日期窗口
    if args.backfill_days > 0:
        start = min(end, today) - timedelta(days=args.backfill_days - 1)
    else:
        start = date.fromisoformat(existing[-1]["date"])
    if end < start:
        log("窗口为空，无需处理。")
        gh_output(result="noop", added="0", start_date="", end_date="", total=len(by_date))
        return 0

    window = {r["date"]: r for r in recs if start <= date.fromisoformat(r["date"]) <= end}
    log(f"本次窗口 {start} ~ {end}：源提供 {len(window)} 条")

    # 幂等合并：已存在且不冲突 → 跳过；冲突 → 默认保留原值并告警
    added, conflicts, skipped = [], [], 0
    for d_iso, rec in sorted(window.items()):
        old = by_date.get(d_iso)
        if old is None:
            added.append({k: rec[k] for k in ORDER})
        else:
            same = all(old.get(k) == rec.get(k) for k in ORDER[1:])
            if same:
                skipped += 1
            else:
                conflicts.append(d_iso)
                if args.overwrite:
                    by_date[d_iso] = {k: rec[k] for k in ORDER}
                    added.append({k: rec[k] for k in ORDER})

    if skipped:
        log(f"已存在且一致，跳过 {skipped} 天")
    if conflicts:
        log(f"[警告] {len(conflicts)} 天数据与源不一致" +
            ("（--overwrite 已覆盖）" if args.overwrite else "（保留仓库原值）") +
            f"：{conflicts[:10]}{' …' if len(conflicts) > 10 else ''}")

    if added:
        by_date.update({r["date"]: r for r in added})

    merged = sorted(by_date.values(), key=lambda x: x["date"])

    # 全量复核：确保合并结果每一条都合规（含历史存量）
    for rec in merged:
        ok, reason = validate_record(rec, today)
        if not ok:
            log(f"[错误] 合并后校验失败 {rec.get('date')}：{reason}")
            gh_output(result="failed", added="0", reason="post-merge-validation")
            return 1

    # 缺口检查：统计 [新增起点的前一日, end] 区间缺失情况
    base = start if args.backfill_days > 0 else date.fromisoformat(existing[-1]["date"])
    have = {r["date"] for r in merged}
    missing, cur = [], base + timedelta(days=1)
    while cur <= end:
        if cur.isoformat() not in have:
            missing.append(cur.isoformat())
        cur += timedelta(days=1)
    span = max((end - base).days, 1)
    ratio = len(missing) / span
    if missing:
        tolerated = len(missing) <= args.max_missing or ratio <= MAX_MISSING_RATIO
        label = "容忍（多为官方停发）" if tolerated else "超限"
        log(f"窗口内缺失 {len(missing)}/{span} 天（{ratio:.1%}）— {label}")
        if missing:
            log(f"  缺失日期：{missing[:20]}{' …' if len(missing) > 20 else ''}")
        if not tolerated:
            log("[错误] 缺口超出容忍阈值，判定为采集异常，终止且不写入")
            gh_output(result="failed", added="0", reason="gap-exceeded", missing=len(missing))
            return 1
        # 源窗口覆盖不到的早期缺口，说明超出 ~49 天窗口，仅提示
        if args.backfill_days > 0 and missing and missing[0] < recs[0]["date"]:
            log("  [提示] 最早的缺口早于源返回的最早日期，"
                "大概率已超出 sina.cn 约 49 天窗口，需增强源或人工补录。")

    if not added:
        log("\n无新增数据。")
        gh_output(result="noop", added="0", start_date="", end_date="",
                  total=len(merged), missing=len(missing))
        gh_summary("西安地铁客流 — 无新增", [
            f"仓库现有 {len(merged)} 条（{merged[0]['date']} ~ {merged[-1]['date']}）",
            f"窗口缺失 {len(missing)} 天",
        ])
        return 0

    s_date, e_date = added[0]["date"], added[-1]["date"]
    log(f"\n新增 {len(added)} 天：{s_date} ~ {e_date}")

    if args.dry_run:
        log("[dry-run] 校验全部通过，未写入文件。")
        for r in added[:10]:
            log(f"  {r['date']}  total={r['total']}")
        if len(added) > 10:
            log(f"  … 另有 {len(added) - 10} 天")
        gh_output(result="dry-run", added=len(added), start_date=s_date, end_date=e_date,
                  total=len(merged), missing=len(missing))
        return 0

    try:
        write_atomic(merged)
    except PipelineError as exc:
        log(f"[错误] 写入失败：{exc}")
        gh_output(result="failed", added="0", reason="write-failed")
        return 1
    log(f"已写入 {DATA_JS}：{len(merged)} 条（{merged[0]['date']} ~ {merged[-1]['date']}）")

    if sync_readme(len(merged)):
        log(f"已同步 {README_MD} 天数为 {len(merged)}")

    gh_output(result="success", added=len(added), start_date=s_date, end_date=e_date,
              total=len(merged), missing=len(missing),
              full_start=merged[0]["date"], full_end=merged[-1]["date"])
    gh_summary("西安地铁客流 — 采集完成", [
        f"新增 **{len(added)}** 天：`{s_date}` ~ `{e_date}`",
        f"数据文件现有 **{len(merged)}** 条：`{merged[0]['date']}` ~ `{merged[-1]['date']}`",
        f"窗口内缺失 {len(missing)} 天" + (f"（{'、'.join(missing[:5])}…）" if missing else ""),
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
