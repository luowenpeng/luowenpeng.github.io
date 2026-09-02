<template>
  <!-- 无可见模板：日期元素由脚本注入到文章 h1 之后 -->
  <span style="display: none" aria-hidden="true"></span>
</template>

<script setup>
// 文章日期注入：在每篇文章 h1 标题下方插入「发布于 / 更新于」
// 数据来源：文章 frontmatter 的 date（发布）与 modified（更新）
// 行为对齐旧 Docsify 插件（hook.doneEach + insertAdjacentElement）
import { useData } from 'vitepress'
import { watch, onMounted, nextTick } from 'vue'

const { page, frontmatter } = useData()

function render() {
  nextTick(() => {
    // 清理旧注入（防止路由切换后残留）
    document.querySelectorAll('.article-dates').forEach((el) => el.remove())

    const fm = frontmatter.value
    if (!fm || !fm.date) return // 仅处理带日期 frontmatter 的文章页
    if (page.value.isNotFound) return

    const h1 =
      document.querySelector('.content-container h1') ||
      document.querySelector('.vp-doc h1')
    if (!h1) return

    const pub = String(fm.date).slice(0, 10)
    const mod = fm.modified ? String(fm.modified).slice(0, 10) : pub

    const div = document.createElement('div')
    div.className = 'article-dates'
    let html = `<span class="article-date-pub">发布于 ${pub}</span>`
    if (mod !== pub) html += `<span class="article-date-mod">更新于 ${mod}</span>`
    div.innerHTML = html
    h1.insertAdjacentElement('afterend', div)
  })
}

onMounted(render)
watch(() => page.value.path, render)
</script>
