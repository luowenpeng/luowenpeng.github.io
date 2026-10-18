# 西安地铁客流数据 — 自动化采集运维说明

> 面向 `luowenpeng.github.io` 仓库。配置一次后每周三自动运行，无需人工干预。

## 1. 架构与数据流

```
                        ┌───────────────── GitHub Actions ─────────────────┐
微博 @西安地铁运营分公司                                                  
        │                                                                 
        ▼                                                                 
sina.cn 媒体聚合页 ──► update-metro-data.py ──► 校验+合并 ──► 原子写入     
（免登录，约49天窗口）      │                                    │        
                          │ 可选增强                            ▼        
m.weibo.cn API（需Cookie）─┘                    docs/public/metro-passenger-data.js
                                                              │           
                                                git commit/push（PAT）│   
                                                              ▼           
                                                    deploy.yml 触发构建    
                                                              ▼           
                                              https://luowenpeng.com/metro-passenger-flow.html
                        └──────────────────────────────────────────────────┘
```

**链路要点**：栏目页 `metro-passenger-flow.html` 通过 `<script src="metro-passenger-data.js">`
动态读取全局变量 `window.metroPassengerData`，页面本身无任何硬编码日期或天数。
因此**只需替换数据文件，栏目页自动同步**，无需改动 HTML。

## 2. 文件清单

| 文件 | 作用 |
| --- | --- |
| `scripts/update-metro-data.py` | 采集/校验/合并/写入主脚本（零第三方依赖，标准库实现） |
| `.github/workflows/update-metro-data.yml` | 定时 + 手动触发的工作流，含失败告警 |
| `.github/workflows/deploy.yml` | 原有部署流，由数据推送自动级联触发 |
| `docs/public/metro-passenger-data.js` | 数据文件（自动生成，勿手改） |
| `docs/public/metro-passenger-flow.html` | 栏目页（无需改动） |
| `README.md` | 首页「N 天全网客流」由脚本同步更新 |

## 3. 需要的密钥与权限

### 3.1 必需

| Secret 名 | 类型 | 用途 | 权限要求 |
| --- | --- | --- | --- |
| `METRO_SYNC_TOKEN` | Personal Access Token | 推送数据、创建告警 Issue、吊销写渊 | Fine-grained PAT：选中本仓库，`Contents: Read and write`、`Issues: Read and write`、`Metadata: Read-only` |

> **为什么必须用 PAT 而不是默认的 `GITHUB_TOKEN`**
> GitHub 官方限制：由 `GITHUB_TOKEN` 发起的 push **不会**级联触发其他工作流。
> 若使用 `GITHUB_TOKEN`，`deploy.yml` 不会被触发 → 仓库数据更新了，但网站页面永远不变。
> 工作流中已内置检测，若未探测到部署触发会打印 `::warning::` 提示。

**获取路径**：GitHub → Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token → Repository access: `luowenpeng.github.io`。

**录入路径**：仓库 → Settings → Secrets and variables → Actions → New repository secret。

### 3.2 可选

| Secret 名 | 用途 |
| --- | --- |
| `WEIBO_COOKIE` | 增强源 Cookie（`SUB=...`），用于补齐主源窗口之外的历史数据。未配置时自动跳过，不影响正常运行 |

### 3.3 自动处理（无需手工）

告警标签 `metro-data-alert` 会在首次告警时自动创建。

## 4. 调度策略

```yaml
cron: '17 2 * * 3'   # UTC 每周三 02:17 = 北京时间每周三 10:17
```

- 选 17 分而非整点，避开 GitHub Actions 调度高峰，实际延迟更小。
- 每周间隔 7 天，**远小于数据源约 49 天的窗口**，正常情况下不会漏数据。

## 5. 手动运行

仓库 → Actions → **西安地铁客流数据更新** → Run workflow：

| 入参 | 说明 | 常用值 |
| --- | --- | --- |
| `end_date` | 截止日期（含） | 留空 = 昨天 |
| `backfill_days` | 回溯补录天数 | `0` 仅追尾；`45` 补最近一个半月；`7` 补一周 |
| `dry_run` | 只校验不提交 | 先勾选跑一次再看正式跑 |
| `overwrite` | 源与仓库冲突时以源为准 | 默认不勾选（保留仓库原值） |
| `max_missing` | 容忍缺失天数 | 默认 `3`，调大可放宽（配合大范围回填使用） |

本地等价命令（仓库根目录）：

```bash
python3 scripts/update-metro-data.py --dry-run                 # 演练
python3 scripts/update-metro-data.py                            # 正式
python3 scripts/update-metro-data.py --backfill-days 45         # 补最近 45 天
python3 scripts/update-metro-data.py --end 2026-10-17           # 指定截止
```

## 6. 健壮性设计

### 幂等
以 `date` 为主键合并。已存在且值一致 → 跳过；值不一致 → 默认保留仓库原值并告警
（除非 `--overwrite`）。同一份输入重复执行，产出文件**字节级一致**，可安全重跑。

### 不写脏数据
- 逐条校验：日期合法性与未来值、`total` 与分线取值范围、12 条线路齐全、
  分线和与 `total` 偏差 ≤ 0.2。
- 单条不合格 → 丢弃该条，其余照常入库，并记录丢弃原因。
- 整批异常（源不可达 / 0 条有效 / 缺口超限 / 写入校验失败）→ 退出码 1，
  **不修改任何文件**；写盘采用「临时文件 → 回读比对 → `os.replace` 原子替换」。

### 容忍官方停发
数据源偶发停发客流微博。实证缺发：**2026-09-15、2026-09-22**。
缺口 ≤ 3 天或占比 ≤ 10% 视为正常并记录；超出才判失败。
因此**真实存在的数据缺口不会被伪造数据填补**，图表展示的是真实缺失。

### 失败告警
工作流失败或被取消时，自动在仓库创建带 `metro-data-alert` 标签的 Issue；
已有未关闭的同标签 Issue 则追加评论，避免刷屏。

## 7. 已知边界（重要）

1. **主源窗口约 49 天**：`sina.cn` 媒体页**不支持翻页**（实测 `?page=2` 返回完全相同内容），
   只含最近约 47 条客流微博。若任务中断超过 45 天，窗口外的日期将**永久无法通过主源补齐**，
   届时需配置 `WEIBO_COOKIE` 走增强源，或人工补录。
2. **发布延迟约 1 天**：官方一般在次日发布前一日数据。默认截止「昨天」，
   因此最新一天经常取不到 —— 这属于正常情况，下次运行会自动补上，无需处理。
3. **增强源未经充分实测**：`WEIBO_COOKIE` 路径为可选能力，
   若抓取异常会打印警告并自动降级为主源结果，不影响主流程。

## 8. 故障排查速查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 摘要显示「无新增数据」 | 官方未发布/已最新 | 正常，无需处理 |
| `::warning:: 未检测到 deploy.yml 触发` | 用了 `GITHUB_TOKEN` 而非 PAT | 改用 `METRO_SYNC_TOKEN` |
| 「页面结构异常」 | 数据源改版 | 调整 `update-metro-data.py` 中的正则 |
| 「缺口超出容忍阈值」 | 长时间未运行（超 45 天） | 先配 `WEIBO_COOKIE`，或人工补录后重跑 |
| 推 yuilter 冲突 | 本地有未推送的新提交 | 工作流已内置 3 次 rebase 重试 |
