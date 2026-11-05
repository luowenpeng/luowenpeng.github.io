#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""北京双单位客流 —— 本机一键采集并推送。

背景：@北京地铁 与 @京港地铁 的 `searchProfile` 时间窗检索强制需要登录态 Cookie。
按重庆、南京、郑州、合肥的既有做法统一改为**本机执行**：本机
`.cache/weibo-cookie.txt` 直接可用，不上传凭据、不依赖云端密钥。

与其他管道的差异：
  - 总量来自两家正文，无需 OCR；分线明细来自北京地铁配图 OCR（本机已装
    rapidocr-onnxruntime，缺失时脚本自动降级，只产总量 + 京港 5 线）。
  - 「4号线大兴线贯通客运量」是交叉口径，不计入京港合计，也不计入两家合计。
  - 两家当日都发布才写 total，缺一家时合计留空。

用法：
    python scripts/local-bj-metro-sync.py             # 采集 → 校验 → 提交 → 推送
    python scripts/local-bj-metro-sync.py --dry-run   # 只演练，不写文件、不提交

幂等：采集脚本以日期为主键合并，已存在的日期自动跳过；OCR 结果按微博 ID 缓存，
重复运行不会重复下载或识别。配合 WorkBuddy 自动化每周一 21:00 调用
（重庆 17:00 / 南京 18:00 / 郑州 19:00 / 合肥 20:00 / 北京 21:00 错峰）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA = "docs/public/bj-metro-passenger-data.js"
COLLECTOR = "scripts/update-bj-metro-data.py"
COOKIE_FILE = REPO / ".cache" / "weibo-cookie.txt"
LOG_FILE = REPO / ".cache" / "bj-local-sync.log"


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
        cmd, cwd=REPO, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="北京双单位客流：本机采集并推送到博客仓库")
    ap.add_argument("--dry-run", action="store_true", help="只演练采集，不写文件、不提交")
    ap.add_argument("--backfill-days", default="14", help="回溯补录天数（默认 14）")
    ap.add_argument("--max-missing", default="6", help="允许的最大缺口天数（默认 6）")
    ap.add_argument("--no-ocr", action="store_true", help="跳过配图 OCR（总量不受影响）")
    args = ap.parse_args()

    log(f"\n===== {dt.datetime.now():%Y-%m-%d %H:%M:%S} 北京客流本机同步 =====")
    log(f"仓库：{REPO}")

    if not COOKIE_FILE.exists() or not COOKIE_FILE.read_text(encoding="utf-8").strip():
        log(f"[失败] 缺少微博 Cookie：{COOKIE_FILE}")
        log("       请从浏览器登录后更新该文件，采集脚本会自行读取（也可用 WEIBO_COOKIE 环境变量）。")
        return 1

    env = os.environ.copy()
    env["WEIBO_COOKIE"] = COOKIE_FILE.read_text(encoding="utf-8").strip()

    cmd = [sys.executable, COLLECTOR,
           "--backfill-days", args.backfill_days,
           "--max-missing", args.max_missing]
    if args.no_ocr:
        cmd.append("--no-ocr")
    if args.dry_run:
        cmd.append("--dry-run")
    log("$ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    log("\n".join((r.stdout or "").strip().splitlines()[-30:]))
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
             f"data: 北京轨道客流数据本机采集 {dt.date.today():%Y-%m-%d}"])
    if r.returncode != 0:
        log("[失败] git commit 失败：" + (r.stdout or "") + (r.stderr or ""))
        return 1

    r = run(["git", "pull", "--rebase", "--autostash", "origin", "master"])
    if r.returncode != 0:
        log("[失败] git pull --rebase 失败：" + (r.stdout or "") + (r.stderr or ""))
        log("       若提示网络错误，检查 Clash 是否在跑（git 全局已配 http.proxy=127.0.0.1:7897）。")
        return 1

    r = run(["git", "push", "origin", "master"])
    if r.returncode != 0:
        # 实测 2026-11-05：Clash 对 git-receive-pack 偶发 curl 52 空响应，
        # 而同网络直连可成功。保留全局代理配置，仅在此处临时覆盖重试一次。
        log("[提示] 代理推送失败，改用直连重试一次 …")
        r = run(["git", "-c", "http.proxy=", "-c", "https.proxy=",
                 "push", "origin", "master"])
    log((r.stdout or "") + (r.stderr or ""))
    if r.returncode != 0:
        log("[失败] git push 失败（代理与直连均不可用）；检查网络后重跑本脚本即可（采集幂等）。")
        return 1

    log("[完成] 采集与推送成功。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        log(f"[异常] {exc}")
        sys.exit(1)
