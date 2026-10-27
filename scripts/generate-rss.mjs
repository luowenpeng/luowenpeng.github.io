// 生成 RSS 2.0 feed：读取 docs/*.md 的 frontmatter（title/date/description），
// 输出 docs/.vitepress/dist/feed.xml。挂接在 npm run build 之后（postbuild），
// 本地与 GitHub Actions 均自动生效。
import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const DOCS_DIR = join(__dirname, '..', 'docs')
const DIST_DIR = join(DOCS_DIR, '.vitepress', 'dist')

const SITE = 'https://luowenpeng.com'
const CHANNEL_TITLE = 'Wenpeng LUO'
const CHANNEL_DESC = '雒文鹏的个人博客 — 诚意正心·修身齐家。学习心得、工作积累、代码项目与类比修辞收集。'

const esc = (s) => String(s ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')

function parseFrontmatter(src) {
  const m = src.match(/^---\r?\n([\s\S]*?)\r?\n---/)
  if (!m) return null
  const fm = {}
  for (const line of m[1].split(/\r?\n/)) {
    const kv = line.match(/^([A-Za-z_-]+):\s*(.*)$/)
    if (kv) fm[kv[1]] = kv[2].trim().replace(/^["']|["']$/g, '')
  }
  return fm
}

// 递归收集 md 文件（含子目录如 metro-weekly/），排除 public/
function collectMd(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name)
    if (e.isDirectory()) return (e.name === 'public' || e.name === 'topics') ? [] : collectMd(p)
    return e.name.endsWith('.md') && e.name !== 'index.md' ? [p] : []
  })
}

const items = collectMd(DOCS_DIR)
  .map((p) => {
    const rel = p.slice(DOCS_DIR.length + 1).replace(/\\/g, '/')
    const src = readFileSync(p, 'utf8')
    const fm = parseFrontmatter(src) || {}
    const slug = rel.replace(/\.md$/, '').split('/').map(encodeURIComponent).join('/')
    const date = fm.date ? new Date(fm.date) : null
    return {
      title: fm.title || rel.replace(/\.md$/, ''),
      link: `${SITE}/${slug}`,
      description: fm.description || '',
      pubDate: date && !isNaN(date) ? date.toUTCString() : null,
      _ts: date && !isNaN(date) ? date.getTime() : 0,
    }
  })
  // 无 pubDate 的条目（如 about.md 这类静态页）不符合 RSS 排序要求，不进 feed
  .filter((it) => it.pubDate !== null)
  .sort((a, b) => b._ts - a._ts)

// 不引用 XSLT 样式表：Chrome 正在移除 XSLT，带 <?xml-stylesheet?> 的 feed
// 会在浏览器里弹出弃用横幅；RSS 阅读器本就直接解析 XML，不需要该样式表。
const xml = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>${esc(CHANNEL_TITLE)}</title>
    <link>${SITE}/</link>
    <description>${esc(CHANNEL_DESC)}</description>
    <language>zh-CN</language>
    <atom:link href="${SITE}/feed.xml" rel="self" type="application/rss+xml"/>
${items.map((it) => `    <item>
      <title>${esc(it.title)}</title>
      <link>${esc(it.link)}</link>
      <guid isPermaLink="true">${esc(it.link)}</guid>
      ${it.pubDate ? `<pubDate>${it.pubDate}</pubDate>` : ''}
      <description>${esc(it.description)}</description>
    </item>`).join('\n')}
  </channel>
</rss>
`

if (!existsSync(DIST_DIR)) {
  console.error('dist 目录不存在，请先运行 vitepress build')
  process.exit(1)
}
writeFileSync(join(DIST_DIR, 'feed.xml'), xml, 'utf8')
console.log(`feed.xml 已生成：${items.length} 篇文章 → ${join(DIST_DIR, 'feed.xml')}`)
