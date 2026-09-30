import DefaultTheme from 'vitepress/theme'
import type { Theme } from 'vitepress'
import { h } from 'vue'
import Busuanzi from './Busuanzi.vue'
import ArticleMeta from './ArticleMeta.vue'
import SidebarResizer from './SidebarResizer.vue'
// KaTeX 样式本地打包（npm 依赖，无 CDN）
import 'katex/dist/katex.min.css'
import './custom.css'

export default {
  extends: DefaultTheme,
  Layout: () => {
    return h(DefaultTheme.Layout, null, {
      // 侧边栏底部：不蒜子访问量计数器（含防刷逻辑）
      'sidebar-nav-after': () => h(Busuanzi),
      // 文章标题下方：发布/更新日期（组件内部定位到 h1 之后注入）
      'doc-top': () => h(ArticleMeta),
      // 布局底部：侧边栏拖拽调宽手柄
      'layout-bottom': () => h(SidebarResizer),
    })
  },
} satisfies Theme
