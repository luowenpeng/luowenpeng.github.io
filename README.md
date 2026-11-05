# luowenpeng.github.io

> **线上站点：https://luowenpeng.com** — 雒文鹏的个人博客，诚意正心·修身齐家

VitePress 静态站，源码在本仓库，由 GitHub Actions 自动构建发布到 GitHub Pages，自定义域名 luowenpeng.com。

## 站点内容

**文章清单不在本文件维护** —— 单一事实源是 `docs/.vitepress/config.ts`（导航 + 侧边栏）
与各专题页 `docs/topics/*.md`。在 README 里再抄一份必然漂移（历史教训：站点已有 11 篇备考文章时，README 仍停在 4 篇）。

| 板块 | 地址 |
|---|---|---|
| 首页 | https://luowenpeng.com |
| 高考加油站 | https://luowenpeng.com/topics/高考加油站 |
| AI 自习室 | https://luowenpeng.com/topics/AI自习室 |
| 运动健身房 | https://luowenpeng.com/topics/运动健身房 |
| 进化研讨屋 | https://luowenpeng.com/topics/进化研讨屋 |
| 其他 | https://luowenpeng.com/topics/其他 |
| 城轨周报 | 首页侧边栏「🚇 城轨周报」下按年归档 |
| 类比修辞库 | https://luowenpeng.com/analogy-collection.html |
| 西安地铁客流 | https://luowenpeng.com/xa-metro-passenger-flow.html |
| 重庆轨道客流 | https://luowenpeng.com/cq-metro-passenger-flow.html |
| 南京地铁客流 | https://luowenpeng.com/nj-metro-passenger-flow.html |
| 郑州地铁客流 | https://luowenpeng.com/zz-metro-passenger-flow.html |
| 合肥轨道客流 | https://luowenpeng.com/hf-metro-passenger-flow.html |
| 北京轨道客流 | https://luowenpeng.com/bj-metro-passenger-flow.html |
| RSS 订阅 | https://luowenpeng.com/feed.xml |

## 本地开发

```bash
npm ci
npm run dev        # http://localhost:5173
npm run build      # 构建到 docs/.vitepress/dist，并生成 feed.xml
npm run preview
```

## 自动化管线

| 管线 | 触发 | 产物 | 说明 |
|---|---|---|---|
| `deploy.yml` | push `master` / 手动 | GitHub Pages 制品 | `fetch-depth: 0` 是必需的，否则 `lastUpdated` 与 sitemap lastmod 失效 |
| `update-metro-data.yml` | 每周三 10:17 CST / 手动 | `docs/public/metro-passenger-data.js` | 采集→校验→提交→告警，详见 `scripts/README-metro-pipeline.md` |
| `scripts/local-cq-metro-sync.py` | 每周一 17:00 CST（本机 WorkBuddy 自动化） | `docs/public/cq-metro-passenger-data.js` | 抓图→OCR→校验→提交→推送；云端 Actions 出口 IP 被新浪图床屏蔽，采集改在本机执行 |
| `update-cq-metro-data.yml` | 仅手动（云端备用） | 同上 | 应急补跑 / 链路诊断，不设定时；回填历史需 Secret `WEIBO_COOKIE` |
| `scripts/local-nj-metro-sync.py` | 每周一 18:00 CST（本机 WorkBuddy 自动化） | `docs/public/nj-metro-passenger-data.js` | 正文纯文本解析→校验→提交→推送；自带 `sina.cn` 免登录源，Cookie 仅作可选增强，凭据不出本机 |
| `update-nj-metro-data.yml` | 仅手动（云端备用） | 同上 | 应急补跑 / 链路诊断，不设定时；回填历史需 Secret `WEIBO_COOKIE` |
| `scripts/local-zz-metro-sync.py` | 每周一 19:00 CST（本机 WorkBuddy 自动化） | `docs/public/zz-metro-passenger-data.js` | 长微博展开→校验→提交→推送；Cookie 取自本机 `.cache/weibo-cookie.txt`，凭据不出本机 |
| `update-zz-metro-data.yml` | 仅手动（云端备用） | 同上 | 应急补跑 / 链路诊断，不设定时；需 Secret `WEIBO_COOKIE` |
| `scripts/local-hf-metro-sync.py` | 每周一 20:00 CST（本机 WorkBuddy 自动化） | `docs/public/hf-metro-passenger-data.js` | 时间窗检索→三代格式/单位归一→逐日校验→提交→推送；Cookie 取自本机，凭据不出本机 |
| `update-hf-metro-data.yml` | 仅手动（云端备用） | 同上 | 应急补跑 / 链路诊断，不设定时；需 Secret `WEIBO_COOKIE` |
| `scripts/local-bj-metro-sync.py` | 每周一 21:00 CST（本机 WorkBuddy 自动化） | `docs/public/bj-metro-passenger-data.js` | 双单位正文解析→配图 OCR（可选）→两家合计校验→提交→推送；Cookie 取自本机，凭据不出本机 |
| `update-bj-metro-data.yml` | 仅手动（云端备用） | 同上 | 应急补跑 / 链路诊断，不设定时；需 Secret `WEIBO_COOKIE`，分线 OCR 可选开启 |
| 类比修辞库同步 | 由外部仓库 `analogycollection` 的工作流触发 | `docs/public/analogy-collection.html`、`analogy-data.js` | 本仓库无对应 workflow，见下方警告 |

> ⚠️ `docs/public/analogy-collection.html` 与 `analogy-data.js` 是外部推送进来的产物。
> 需要改样式或结构时请到 `analogycollection` 仓库改，直接改本仓库文件会在下次同步时被静默覆盖。

## 目录结构

```
docs/
  *.md                 文章（文件名即 URL slug）
  topics/*.md          五个专题聚合页
  metro-weekly/*.md    城轨周报，文件名带年份前缀防止跨年同名覆盖
  public/              静态资源，构建时原样复制到站点根目录
    analogy-collection.html          类比修辞库子站
    xa-metro-passenger-flow.html    西安客流子站（旧地址 metro-passenger-flow.html 仅作跳转）
    cq-metro-passenger-flow.html    重庆客流子站
    nj-metro-passenger-flow.html    南京客流子站
    zz-metro-passenger-flow.html    郑州客流子站
    hf-metro-passenger-flow.html    合肥轨道客流子站
    bj-metro-passenger-flow.html    北京双单位客流子站
    metro-passenger-data.js         西安客流数据（自动生成，勿手改）
    cq-metro-passenger-data.js      重庆客流数据（自动生成，勿手改）
    nj-metro-passenger-data.js      南京客流数据（自动生成，勿手改）
    zz-metro-passenger-data.js      郑州客流数据（自动生成，勿手改）
    hf-metro-passenger-data.js      合肥客流数据（自动生成，勿手改）
    bj-metro-passenger-data.js      北京双单位客流数据（自动生成，勿手改）
  .vitepress/
    config.ts          导航、侧边栏、OG 与 sitemap 生成
    theme/             自定义组件与样式
scripts/
  update-metro-data.py  西安客流采集脚本（零第三方依赖）
  update-cq-metro-data.py  重庆客流采集脚本（需 OCR，rapidocr-onnxruntime）
  update-nj-metro-data.py  南京客流采集脚本（正文纯文本解析，零第三方依赖）
  nj-metro-manual.json     南京客流人工补录基线（接口检索不到的日期，支持 _override 强制校正）
  local-nj-metro-sync.py   南京本机一键采集推送（WorkBuddy 每周一 18:00）
  update-zz-metro-data.py  郑州客流采集脚本（长微博 longtext 展开，零第三方依赖）
  zz-metro-manual.json     郑州客流人工补录基线
  local-zz-metro-sync.py   郑州本机一键采集推送（WorkBuddy 每周一 19:00）
  update-hf-metro-data.py  合肥客流采集脚本（三代正文格式 / 单位归一，零第三方依赖）
  hf-metro-manual.json     合肥客流人工补录基线（支持 _override 强制校正）
  local-hf-metro-sync.py   合肥本机一键采集推送（WorkBuddy 每周一 20:00）
  update-bj-metro-data.py  北京双单位采集脚本（正文解析 + 配图 OCR，需 rapidocr-onnxruntime）
  bj-metro-manual.json     北京双单位人工补录基线（支持 _override 强制校正）
  local-bj-metro-sync.py   北京本机一键采集推送（WorkBuddy 每周一 21:00）
  local-cq-metro-sync.py   重庆本机一键采集推送（WorkBuddy 每周一 17:00）
  generate-rss.mjs      构建后生成 feed.xml
  gen-og-image.py       手动生成 docs/public/og-image.png
```

## 部署要点

- **不要删除根目录的 `CNAME` 与 `.nojekyll`**。随制品发布的是 `docs/public/CNAME`，但根目录这两份是 GitHub 侧的保险：两者任一缺失，GitHub 会解绑自定义域名或退回用 Jekyll 渲染仓库根目录，站点会直接 404（2026-10-27 实际踩过一次）。
- 新增文章检查清单：写 `docs/*.md` → 登记到 `config.ts` 导航与侧边栏 → 在对应 `docs/topics/*.md` 补条目。三者缺一，就会出现「文章在线但站内找不到」。
- **南京周更也改在本机执行（四条管道里唯一有免登录兜底的一条）**：`scripts/update-nj-metro-data.py` 自带 `sina.cn` 媒体页免登录源（约最近 100 条 ≈ 20 天），回溯 14 天的周更**完全不依赖 Cookie**；本机若有 `.cache/weibo-cookie.txt` 则 `--source auto` 自动双源合并（时间线 + 搜索 + 时间窗），数据更全，Cookie 缺失不判失败。由 `scripts/local-nj-metro-sync.py` 本机跑（WorkBuddy 每周一 18:00），云端 `update-nj-metro-data.yml` 已去掉 `schedule`、仅留手动应急入口。
- **郑州周更在本机执行（同重庆），凭据不出本机**：检索接口 `searchProfile` 免登录返回 `ok=-100`，`m.weibo.cn` 时间线免登录返回 432，`sina.cn` 源只给截断正文且仅覆盖约 5 天；只有 `m.weibo.cn/statuses/extend|show` 两个**展开**接口免 Cookie 可用（作降级），但拿不到微博列表，所以没有免登录退路（南京有 `sina.cn` 免登录源、郑州不行）。与其把微博 Cookie 上传成仓库 Secret，改由 `scripts/local-zz-metro-sync.py` 本机跑（WorkBuddy 每周一 19:00），读取 `.cache/weibo-cookie.txt`；Cookie 过期时只需更新该文件。云端 `update-zz-metro-data.yml` 仅为手动应急入口，未配置 Secret 时手动触发会在「检查 Cookie」报错，属预期。
- **合肥周更同样在本机执行，凭据不出本机**：`searchProfile` 需登录态 Cookie，由 `scripts/local-hf-metro-sync.py` 读取 `.cache/weibo-cookie.txt`（WorkBuddy 每周一 20:00），云端 `update-hf-metro-data.yml` 仅留手动应急入口。合肥正文换过三代格式（2018「客流日报」累计句 + 当日值 / 2019–2022「客流信息」多为总量、单位含乘次·人次·万乘次 / 2023 起「客流播报」总量 + 5~8 线），采集以「日期紧邻线网客运量」锁定当日值、早期原始计数统一换算万人次，并排除累计句、假期汇总、单月新高、跨年纪录与专题长文。官方原文本身分线合计 ≠ 总量的 3 天按原值收录并留痕，不做平衡修正。
- **北京是唯一「双运营单位」管道，合计不等于全网**：全路网 30 条线路 / 909 km / 540 座运营车站由 5 家企业分营，只有 @北京地铁（uid 2778292197，北京市地铁运营有限公司）与 @京港地铁（uid 2125939111，北京京港地铁有限公司）逐日发布所辖线路客流；19号线、燕房线、大兴机场线、西郊线、亦庄 T1 等不发布。北京地铁正文直接给所辖 19 线总量、各线数值只在配图柱状图里（需 OCR）；京港正文给 4号线 / 大兴线 / 14 / 16 / 17 号线 5 个分线值，求和即公司总量。**「4号线大兴线贯通客运量」是跨线贯通的交叉口径**，单独存 `through` 供参考，不计入任何合计；只有两家当日都发布才写 `total`，否则合计留空、折线断开。两家均自 `2026-05-05` 才开始逐日发布，更早区间检索为空（非索引限制）。分线 OCR 以「19 个柱顶数值之和 == 正文总量」做硬校验，不通过则丢弃当日分线、保留总量。周更由 `scripts/local-bj-metro-sync.py` 本机执行（WorkBuddy 每周一 21:00），云端仅留手动入口。
- 微博 `searchProfile` 索引边界**按账号而异**：郑州账号只保留约最近 12 个月（无法回填到 2025-11 之前），而 @合肥轨道交通 可回溯到 2018-01 前后，但 `2018-05~2019-09`、`2022-11~2023-02` 存在官方停发或索引成片缺口，属正常留空、不插值。时间窗参数名是 `starttime`/`endtime`（**北京时间 epoch**），写成 `start_time` 或 `timescope` 会被静默忽略；数据日 D 的微博发布于 D+1 早晨，检索窗口需整体后移一天，否则区间最后一天永远抓不到。

## 版权协议

[CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/deed.zh)（见 `LICENSE`）——可自由转载分享，需署名、非商业、不得演绎。
