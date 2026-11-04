#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""郑州地铁客流 —— 本机一键采集并推送。

背景：郑州官方的每日客流微博**全部是长微博**，检索接口 searchProfile 强制需要
登录态 Cookie（免登录返回 ok=-100）；m.weibo.cn 时间线免登录返回 432，
sina.cn 源也只给截断正文且仅覆盖约 5 天。因此整条管道没有免登录退路。
把 Cookie 放进 GitHub Secret 固然可行，但按重庆的既有做法统一改为**本机执行**：
本机 .cache/weibo-cookie.txt 直接可用，不上传凭据、不依赖云端密钥。

用法：
    python scripts/local-zz-metro-sync.py             # 采集 → 校验 → 提交 → 推送
    python scripts/local-zz-metro-sync.py --dry-run   # 只演练，不写文件、不提交

幂等：采集脚本以日期为主键合并，已存在的日期自动跳过，重复运行不会产生脏数据。
配合 WorkBuddy 自动化每周一 19:00 调用；也可随时手动运行。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA = "docs/public/zz-metro-passenger-data.js"
COLLECTOR = "scripts/update-zz-metro-data.py"
COOKIE_FILE = REPO / ".cache" / "weibo-cookie.txt"
LOG_FILE = REPO / ".cache" / "zz-local-sync.log"


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
    ap = argparse.ArgumentParser(description="郑州地铁客流：本机采集并推送到博客仓库")
    ap.add_argument("--dry-run", action="store_true", help="只演练采集，不写文件、不提交")
    ap.add_argument("--backfill-days", default="14", help="回溯补录天数（默认 14）")
    ap.add_argument("--max-missing", default="6", help="允许的最大缺口天数（默认 6）")
    args = ap.parse_args()

    log(f"\n===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} 郑州客流本机同步 =====")
    log(f"仓库：{REPO}")

    # 检索接口强制依赖 Cookie，先自检，避免自动化里出现含糊的失败。
    if not COOKIE_FILE.exists() or not COOKIE_FILE.read_text(encoding="utf-8").strip():
        log(f"[失败] 缺少微博 Cookie：{COOKIE_FILE}")
        log("       请从浏览器登录后更新该文件，采集脚本会自行读取（也可用 WEIBO_COOKIE 环境变量）。")
        return 1

    # 采集脚本从 cwd 读取 .cache/weibo-cookie.txt，并把长文全文写入本地缓存。
    # 这里再显式注入环境变量（覆盖而非 setdefault），确保本机周更一定用这份
    # 刚自检过的 Cookie，不会被系统里残留的旧 WEIBO_COOKIE 抢先。
    env = os.environ.copy()
    env["WEIBO_COOKIE"] = COOKIE_FILE.read_text(encoding="utf-8").strip()

    cmd = [sys.executable, COLLECTOR,
           "--backfill-days", args.backfill_days,
           "--max-missing", args.max_missing]
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

    # 只暂存这一份数据文件，绝不把工作区里其他改动带进提交。
    if run(["git", "add", DATA]).returncode != 0:
        log("[失败] git add 失败")
        return 1

    if run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        log("无新增数据，跳过提交。")
        return 0

    r = run(["git", "commit", "-m",
             f"data: 郑州地铁客流数据本机采集 {dt.date.today():%Y-%m-%d}"])
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
