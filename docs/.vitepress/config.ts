import { defineConfig } from 'vitepress'
import { katex } from '@mdit/plugin-katex'

// 旧 Docsify 链接兼容：#/docs/xxx → /xxx（注入到 <head>，早于路由初始化执行）
const legacyHashRedirect = `
(function () {
  var h = location.hash || '';
  if (h.indexOf('#/') !== 0) return;
  var p = h.slice(2);
  if (p === '' || p === 'README' || p === 'README.md') { location.replace('/'); return; }
  if (p.indexOf('docs/') === 0) p = p.slice(5);
  p = p.replace(/\\.md$/, '').replace(/\\/$/, '');
  if (p === '' || p === 'README') { location.replace('/'); return; }
  location.replace('/' + p);
})();
`

export default defineConfig({
  lang: 'zh-CN',
  title: 'Wenpeng LUO',
  description: '雒文鹏的个人博客 — 诚意正心·修身齐家。学习心得、工作积累、代码项目与类比修辞收集。',
  cleanUrls: true,
  lastUpdated: true,
  // sitemap 构建时自动生成（含 lastmod，依赖完整 git 历史）——替代手写 public/sitemap.xml
  sitemap: {
    hostname: 'https://luowenpeng.com',
    transformItems(items) {
      // public/ 独立页不在 VitePress 路由表内，手动追加
      items.push(
        { url: 'https://luowenpeng.com/analogy-collection.html', changefreq: 'weekly' },
        { url: 'https://luowenpeng.com/metro-passenger-flow.html', changefreq: 'daily' },
      )
      return items
    },
  },
  head: [
    ['link', { rel: 'icon', href: '/favicon.ico' }],
    // 字体：Inter（正文 UI）+ Noto Serif SC（中文标题衬线），国内镜像
    ['link', { rel: 'preconnect', href: 'https://fonts.loli.net' }],
    ['link', { rel: 'stylesheet', href: 'https://fonts.loli.net/css2?family=Inter:wght@400;500;600&family=Noto+Serif+SC:wght@500;600;700&display=swap' }],
    // OG / Twitter 基础元标签
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:site_name', content: 'Wenpeng LUO' }],
    ['meta', { property: 'og:title', content: 'Wenpeng LUO — 诚意正心·修身齐家' }],
    ['meta', { property: 'og:description', content: '雒文鹏的个人博客：学习心得、工作积累、代码项目与类比修辞收集库。' }],
    ['meta', { property: 'og:image', content: 'https://luowenpeng.com/og-image.png' }],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
    ['meta', { name: 'twitter:image', content: 'https://luowenpeng.com/og-image.png' }],
    // GoatCounter 访问统计（免费、无 Cookie、不采集个人信息）
    ['script', { 'data-goatcounter': 'https://luowp.goatcounter.com/count', async: true, src: 'https://gc.zgo.at/count.js' }],
    // 旧 Docsify hash 链接重定向
    ['script', {}, legacyHashRedirect],
  ],
  markdown: {
    config(md) {
      md.use(katex)
    },
  },
  themeConfig: {
    nav: [
      { text: '首页', link: '/' },
      {
        text: '文章',
        items: [
          { text: '高考加油站', link: '/topics/高考加油站' },
          { text: 'AI 自习室', link: '/topics/AI自习室' },
          { text: '运动健身房', link: '/topics/运动健身房' },
          { text: '进化研讨屋', link: '/topics/进化研讨屋' },
          { text: '其他', link: '/topics/其他' },
        ],
      },
      { text: '城轨周报', link: '/metro-weekly/城轨周报-001' },
      // 独立静态页（public/ 产物）不在 VitePress 路由表内，必须用完整 URL 绕过 SPA 路由，否则点击 404
      { text: '类比修辞库', link: 'https://luowenpeng.com/analogy-collection.html' },
      { text: '西安地铁客流', link: 'https://luowenpeng.com/metro-passenger-flow.html' },
      { text: '关于', link: '/about' },
    ],
    sidebar: {
      '/': [
        {
          text: '🚇 城轨周报',
          items: [
            { text: '城轨周报 · 第 1 期', link: '/metro-weekly/城轨周报-001' },
          ],
        },
        {
          text: '📝 文章',
          items: [
            { text: '高考备考·与乐乐深度谈心手册', link: '/高考备考-与乐乐深度谈心手册' },
            { text: '甘肃省 2025 年高考分数一分一段深度分析', link: '/甘肃省2025年高考分数一分一段深度分析及备考启示' },
            { text: '数学的真相：直觉比逻辑更重要 — 示例补充', link: '/Bessis数学方法论-示例补充' },
            { text: '数学的真相：直觉比逻辑更重要', link: '/Bessis数学方法论-高考应用指南' },
            { text: '翻译《Range》', link: '/translation-of-range' },
            { text: '再读《定投》', link: '/re-read-Regular-Investment' },
            { text: '长江三角洲区域一体化发展规划纲要', link: '/The-Outline-of-the-Regional-Integration-and-Development-of-the-Yangtze-River-Delta' },
          ],
        },
        {
          text: '💻 Code',
          items: [
            { text: '利用 GitHub 建立个人博客网站', link: '/create-my-github-page' },
            { text: '从 404 到 200：一次 GitHub Pages 网站修复实战', link: '/fix-github-pages-404' },
            { text: '博客仓库评估报告 V1.0', link: '/blog-repo-evaluation-v1' },
            { text: '个人网站优化实录（2026-09）', link: '/博客优化总结-2026-09' },
            { text: '个人网站功能台账（Feature Ledger）', link: '/网站功能台账-Feature-Ledger' },
          ],
        },
      ],
    },
    search: {
      provider: 'local',
      options: {
        translations: {
          button: { buttonText: '搜索', buttonAriaLabel: '搜索文档' },
          modal: {
            displayDetails: '显示详细列表',
            resetButtonTitle: '清除查询条件',
            backButtonTitle: '关闭搜索',
            noResultsText: '无法找到相关结果',
            footer: { selectText: '选择', navigateText: '切换', closeText: '关闭' },
          },
        },
      },
    },
    outline: { label: '本页目录', level: [2, 3] },
    docFooter: { prev: '上一篇', next: '下一篇' },
    returnToTopLabel: '回到顶部',
    externalLinkIcon: true,
    lastUpdated: { text: '最后更新' },
  },
})
