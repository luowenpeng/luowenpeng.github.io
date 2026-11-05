#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""南京地铁客流 —— 本机一键采集并推送。

背景：按重庆、郑州、合肥的既有做法，把周更从云端 GitHub Actions 收回本机执行，
凭据不出本机，也不再占用 Actions 并发额度。

与其他三条管道的关键差异：南京采集脚本自带 `sina.cn` 免登录源（最近约 100 条
≈ 20 天），回溯 14 天的周更**没有 Cookie 也能跑**。因此本机 Cookie 文件在这里是
「可选增强」而非硬依赖：
  - 有 Cookie  → --source auto 双源合并（时间线 + 搜索 + 时间窗），数据更全；
  - 无 Cookie  → 自动降级为 sina.cn 免登录源，仍可周更，脚本不判失败。
Cookie 若存在也只在本机读取，不上传成仓库 Secret。

用法：
    python scripts/local-nj-metro-sync.py             # 采集 → 校验 → 提交 → 推送
    python scripts/local-nj-metro-sync.py --dry-run   # 只演练，不写文件、不提交

幂等：采集脚本以日期为主键合并，已存在的日期自动跳过，重复运行不会产生脏数据；
人工补录基线 scripts/nj-metro-manual.json 每次运行自动合并，从零重建也不丢数据。
配合 WorkBuddy 自动化每周一 18:00 调用（重庆 17:00 / 南京 18:00 / 郑州 19:00 / 合肥 20:00 错峰）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA = "docs/public/nj-metro-passenger-data.js"
COLLECTOR = "scripts/update-nj-metro-data.py"
COOKIE_FILE = REPO / ".cache" / "weibo-cookie.txt"
LOG_FILE = REPO / ".cache" / "nj-local-sync.log"


def log(msg: str = "") -> None:
    print(msg, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    return subprocess.run(
        cmd,
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="南京地铁客流：本机采集并推送到博客仓库")
    ap.add_argument("--dry-run", action="store_true", help="只演练采集，不写文件、不提交")
    ap.add_argument("--backfill-days", default="14", help="回溯补录天数（默认 14）")
    ap.add_argument("--max-missing", default="6", help="允许的最大缺口天数（默认 6）")
    args = ap.parse_args()

    log(f"\n===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} 南京客流本机同步 =====")
    log(f"仓库：{REPO}")

    env = os.environ.copy()
    if COOKIE_FILE.exists() and COOKIE_FILE.read_text(encoding="utf-8").strip():
        env["WEIBO_COOKIE"] = COOKIE_FILE.read_text(encoding="utf-8").strip()
        log(f"已加载本机 Cookie：{COOKIE_FILE}（--source auto 双源合并）")
    else:
        env.pop("WEIBO_COOKIE", None)
        log("[提示] 本机无 Cookie，自动降级为 sina.cn 免登录源（约覆盖最近 20 天，足够周更）。")

    cmd = [sys.executable, COLLECTOR,
           "--backfill-days", args.backfill_days,
           "--max-missing", args.max_missing,
           "--source", "auto"]
    if args.dry_run:
        cmd.append("--dry-run")
    log("$ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    log("\n".join((r.stdout or "").strip().splitlines()[-25:]))
    if r.returncode != 0:
        log(f"[失败] 采集退出码 {r.returncode}")
        log((r.stderr or "")[-2000:])
        return 1

    if args.dry_run:
        log("[dry-run] 演练完成，未写文件、未提交。")
        return 0

    r = run(["node", "--check", DATA])
    if r.returncode != 0:
        log("[失败] 数据文件语法校验不通过：" + (r.stderr or ""))
        return 1

    if run(["git", "add", DATA]).returncode != 0:
        log("[失败] git add 失败")
        return 1

    if run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        log("无新增数据，跳过提交。")
        return 0

    r = run(["git", "commit", "-m",
             f"data: 南京地铁客流数据本机采集 {dt.date.today():%Y-%m-%d}"])
    if r.returncode != 0:
        log("[失败] git commit 失败：" + (r.stdout or "") + (r.stderr or ""))
        return 1

    r = run(["git", "pull", "--rebase", "--autostash", "origin", "master"])
    if r.returncode != 0:
        log("[失败] git pull --rebase 失败：" + (r.stdout or "") + (r.stderr or ""))
        log("       若提示网络错误，检查 Clash 是否在跑（git 全局已配 http.proxy=127.0.0.1:7897）。")
        return 1

    r = run(["git", "push", "origin", "master"])
    log((r.stdout or "") + (r.stderr or ""))
    if r.returncode != 0:
        log("[失败] git push 失败；检查代理与网络后重跑本脚本即可（采集幂等）。")
        return 1

    log("[完成] 采集与推送成功。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        log(f"[异常] {exc}")
        sys.exit(1)
