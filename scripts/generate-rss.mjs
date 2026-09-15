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

const items = readdirSync(DOCS_DIR)
  .filter((f) => f.endsWith('.md') && f !== 'index.md')
  .map((f) => {
    const src = readFileSync(join(DOCS_DIR, f), 'utf8')
    const fm = parseFrontmatter(src) || {}
    const slug = encodeURIComponent(f.replace(/\.md$/, ''))
    const date = fm.date ? new Date(fm.date) : null
    return {
      title: fm.title || f.replace(/\.md$/, ''),
      link: `${SITE}/${slug}`,
      description: fm.description || '',
      pubDate: date && !isNaN(date) ? date.toUTCString() : null,
      _ts: date && !isNaN(date) ? date.getTime() : 0,
    }
  })
  .sort((a, b) => b._ts - a._ts)

const xml = `<?xml version="1.0" encoding="UTF-8"?>
<?xml-stylesheet type="text/xsl" href="/rss.xsl"?>
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
