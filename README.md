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
| 西安地铁客流 | https://luowenpeng.com/metro-passenger-flow.html |
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
    analogy-collection.html / metro-passenger-flow.html   两个独立子站
    metro-passenger-data.js                               自动生成，勿手改
  .vitepress/
    config.ts          导航、侧边栏、OG 与 sitemap 生成
    theme/             自定义组件与样式
scripts/
  update-metro-data.py  客流采集主脚本（零第三方依赖）
  generate-rss.mjs      构建后生成 feed.xml
  gen-og-image.py       手动生成 docs/public/og-image.png
```

## 部署要点

- **不要删除根目录的 `CNAME` 与 `.nojekyll`**。随制品发布的是 `docs/public/CNAME`，但根目录这两份是 GitHub 侧的保险：两者任一缺失，GitHub 会解绑自定义域名或退回用 Jekyll 渲染仓库根目录，站点会直接 404（2026-10-27 实际踩过一次）。
- 新增文章检查清单：写 `docs/*.md` → 登记到 `config.ts` 导航与侧边栏 → 在对应 `docs/topics/*.md` 补条目。三者缺一，就会出现「文章在线但站内找不到」。

## 版权协议

[CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/deed.zh)（见 `LICENSE`）——可自由转载分享，需署名、非商业、不得演绎。
