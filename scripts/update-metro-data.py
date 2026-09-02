# -*- coding: utf-8 -*-
"""西安地铁客流数据更新管道（可复用）
================================================
数据源：sina.cn 媒体聚合页（SSR 渲染微博全文，免登录、免 Cookie）
  https://www.sina.cn/media/2372208183
  （页面约含最近 45~47 条客流微博 ≈ 一个半月窗口；若更新间隔超过
   45 天会缺数据，届时需用 m.weibo.cn 登录 Cookie 方案补历史）

用法：
  python update-metro-data.py            # 自动补全到最新（默认排除今天）
  python update-metro-data.py --dry-run  # 只检查不写入
  python update-metro-data.py --end 2026-08-30  # 指定截止日期

产出：更新 docs/public/metro-passenger-data.js
验证：node --check（在仓库根目录）
上线：git commit + push master → GitHub Actions 自动构建部署

字段口径（与既有数据一致）：
  - date/total/line1~line16/linexihu（12 条地铁线 + 西户线，"8号(环)线"归为 line8）
  - total 为微博原文的线网总客流（含西户线）
  - 西户线优先取微博原文值，缺失时按 total - 12线和 回填（差值法已验证与原文一致）
  - 数值统一保留 1 位小数
"""
import requests, re, json, argparse, sys
from datetime import date, timedelta

sys.stdout.reconfigure(encoding='utf-8')

UA_PC = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
DATA_JS = 'docs/public/metro-passenger-data.js'
LINES = ['1', '2', '3', '4', '5', '6', '8', '9', '10', '14', '15', '16']
ORDER = ['date', 'total'] + [f'line{n}' for n in LINES] + ['linexihu']


def fetch_sina_page():
    r = requests.get('https://www.sina.cn/media/2372208183', headers={'User-Agent': UA_PC}, timeout=25)
    r.raise_for_status()
    return r.text


def parse_records(html):
    """从 sina.cn 页面提取 (发布日期, 正文) 并解析客流字段"""
    times = [(m.start(), m.group(1)) for m in re.finditer(r'<div class="time">(\d{4}-\d{2}-\d{2})', html)]
    texts = [(m.start(), m.group(1)) for m in re.finditer(r'<div class="post-text">(.*?)</div>', html, re.DOTALL)]

    def strip_tags(t):
        return re.sub(r'<[^>]+>', '', t).strip()

    parsed = []
    for tpos, tval in times:
        cands = [(pos, txt) for pos, txt in texts if pos > tpos]
        if not cands:
            continue
        _, raw = min(cands, key=lambda x: x[0])
        body = strip_tags(raw)
        if '客流数据' not in body or '线网客流' not in body:
            continue
        pub = date.fromisoformat(tval)
        md = re.search(r'(\d{1,2})月(\d{1,2})日', body)
        mt = re.search(r'线网客流([\d.]+)万人次', body)
        if not (md and mt):
            continue
        month, day = int(md.group(1)), int(md.group(2))
        d = date(pub.year, month, day)
        if d > pub:  # 跨年修正
            d = date(pub.year - 1, month, day)
        rec = {'date': d.isoformat(), 'total': float(mt.group(1))}
        for ln in LINES:
            ml = re.search(rf'{ln}号(?:\(环\))?线([\d.]+)万人次', body)
            if ml:
                v = float(ml.group(1))
                rec[f'line{ln}'] = round(v, 1)
            else:
                rec[f'line{ln}'] = None
        # 西户线：优先取原文；缺失时用差值回填（total - 12线和，已验证与原文一致）
        mx = re.search(r'西户线([\d.]+)万人次', body)
        if mx:
            rec['linexihu'] = round(float(mx.group(1)), 1)
        else:
            s = sum(v for k, v in rec.items() if k.startswith('line') and v is not None)
            rec['linexihu'] = round(rec['total'] - s, 1)
        # 自校验：分线和（含西户）≈ total
        ssum = sum(rec[f'line{n}'] for n in LINES) + rec['linexihu']
        if abs(ssum - rec['total']) > 0.2:
            print(f'  [警告] {rec["date"]} 分线和 {ssum:.1f} 与 total {rec["total"]} 偏差过大，跳过')
            continue
        parsed.append(rec)
    parsed.sort(key=lambda x: x['date'])
    return parsed


def load_existing():
    with open(DATA_JS, 'r', encoding='utf-8') as f:
        content = f.read()
    m = re.search(r'var metroPassengerData = (\[.*\]);', content, re.DOTALL)
    return json.loads(m.group(1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--end', help='截止日期 YYYY-MM-DD（默认：昨天）')
    ap.add_argument('--dry-run', action='store_true', help='只检查不写入')
    args = ap.parse_args()

    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
    existing = load_existing()
    last = date.fromisoformat(existing[-1]['date'])
    print(f'现有: {len(existing)} 条 | 至 {last}')
    print(f'目标: 补至 {end}')

    if end <= last:
        print('数据已是最新，无需更新。')
        return

    print('\n抓取 sina.cn 媒体页...')
    html = fetch_sina_page()
    records = parse_records(html)
    print(f'解析到 {len(records)} 条客流微博（{records[0]["date"]} ~ {records[-1]["date"]}）')

    # 过滤：last 之后、end 之前
    new = [r for r in records if last < date.fromisoformat(r['date']) <= end]
    new = [{k: r[k] for k in ORDER} for r in new]
    if not new:
        print('没有可新增的数据（源覆盖范围内无新记录）。')
        return

    # 完整性：逐日检查无缺口
    need = (end - last).days
    got = len(new)
    print(f'需补 {need} 天，实际新增 {got} 天')
    if got < need:
        have = {r['date'] for r in new}
        missing = []
        d = last + timedelta(days=1)
        while d <= end:
            if d.isoformat() not in have:
                missing.append(d.isoformat())
            d += timedelta(days=1)
        print(f'  [警告] 缺失日期: {missing}')
        print('  （若缺口大，说明超过 sina.cn 约 45 天窗口，需 m.weibo.cn 登录 Cookie 补抓）')
        print('  本次仅合并已有数据继续。')

    merged = existing + new
    # 日期连续性与格式校验
    for i, r in enumerate(merged):
        assert date.fromisoformat(r['date']) == date.fromisoformat(merged[0]['date']) + timedelta(days=i), \
            f'日期断档于第 {i} 条: {r["date"]}'
        for k in ORDER:
            assert r.get(k) is not None, f'{r["date"]} 缺字段 {k}'

    if args.dry_run:
        print('\n[dry-run] 校验全部通过，将新增：')
        for r in new:
            print(f'  {r["date"]} total={r["total"]}')
        return

    header = (
        '// 西安地铁客流数据 — 自动生成，请勿手动编辑\n'
        '// 数据来源：微博 @西安地铁运营分公司\n'
        f'// 记录数：{len(merged)} | 日期范围：{merged[0]["date"]} ~ {merged[-1]["date"]}\n'
        'var metroPassengerData = ' + json.dumps(merged, ensure_ascii=False, indent=2) + ';\n'
    )
    with open(DATA_JS, 'w', encoding='utf-8') as f:
        f.write(header)
    print(f'\n已写入 {DATA_JS}: {len(merged)} 条（{merged[0]["date"]} ~ {merged[-1]["date"]}）')
    print('后续步骤: node --check 校验 → 同步更新 README 天数 → git push 触发部署')


if __name__ == '__main__':
    main()
