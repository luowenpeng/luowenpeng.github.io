#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重庆轨道客流 —— 本机一键采集并推送。

背景：GitHub Actions 的海外出口 IP 被新浪图床（wx*.sinaimg.cn）返回 403，
云端定时任务取不到配图；本机直连新浪图床正常，故采集改在本机执行。

用法：
    python scripts/local-cq-metro-sync.py             # 采集 → 校验 → 提交 → 推送
    python scripts/local-cq-metro-sync.py --dry-run   # 只演练，不写文件、不提交

幂等：采集脚本以日期为主键合并，已存在的日期自动跳过，重复运行不会产生脏数据。
配合 WorkBuddy 自动化每周一 17:00 调用；也可随时手动运行。
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA = "docs/public/cq-metro-passenger-data.js"
COLLECTOR = "scripts/update-cq-metro-data.py"
LOG_FILE = REPO / ".cache" / "cq-local-sync.log"


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
    ap = argparse.ArgumentParser(description="重庆轨道客流：本机采集并推送到博客仓库")
    ap.add_argument("--dry-run", action="store_true", help="只演练采集，不写文件、不提交")
    ap.add_argument("--backfill-days", default="14", help="回溯补录天数（默认 14）")
    args = ap.parse_args()

    log(f"\n===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} 重庆客流本机同步 =====")
    log(f"仓库：{REPO}")

    cmd = [sys.executable, COLLECTOR, "--backfill-days", args.backfill_days]
    if args.dry_run:
        cmd.append("--dry-run")
    r = run(cmd)
    tail = (r.stdout or "").strip().splitlines()[-25:]
    log("\n".join(tail))
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
             f"data: 重庆轨道客流数据本机采集 {dt.date.today():%Y-%m-%d}"])
    if r.returncode != 0:
        log("[失败] git commit 失败：" + (r.stdout or "") + (r.stderr or ""))
        return 1

    r = run(["git", "pull", "--rebase", "--autostash", "origin", "master"])
    if r.returncode != 0:
        log("[失败] git pull --rebase 失败：" + (r.stdout or "") + (r.stderr or ""))
        return 1

    r = run(["git", "push", "origin", "master"])
    log((r.stdout or "") + (r.stderr or ""))
    if r.returncode != 0:
        log("[失败] git push 失败")
        return 1

    log("[完成] 采集与推送成功。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        log(f"[异常] {exc}")
        sys.exit(1)
