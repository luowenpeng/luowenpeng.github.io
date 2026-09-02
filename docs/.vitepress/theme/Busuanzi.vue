<template>
  <div class="busuanzi-counter" v-if="visible">
    统计始于：2026.6.15 累计👀 <span class="busuanzi-value">{{ display }}</span> 次访问
  </div>
</template>

<script setup>
// 不蒜子访问量计数器 — 完整移植自旧 Docsify 插件
// 防刷策略：sessionStorage 同会话去重 + localStorage 1h 冷却缓存
import { ref, onMounted } from 'vue'

const display = ref('…')
const visible = ref(true)

const CACHE_KEY = '_bsz_cache'
const CACHE_TS_KEY = '_bsz_cache_ts'
const COUNTED_KEY = '_bsz_counted'
const COOLDOWN_MS = 60 * 60 * 1000 // 1 小时冷却

function showCached() {
  try {
    const cached = localStorage.getItem(CACHE_KEY)
    if (cached !== null && cached !== 'undefined') {
      display.value = cached
      return true
    }
  } catch (e) { /* localStorage 不可用时静默 */ }
  return false
}

function fetchCount() {
  let timer = null

  function onApiResult(data) {
    const val = data && data.site_pv != null ? String(data.site_pv) : '—'
    display.value = val
    if (timer) clearTimeout(timer)
    try {
      localStorage.setItem(CACHE_KEY, val)
      localStorage.setItem(CACHE_TS_KEY, String(Date.now()))
    } catch (e) { /* 静默 */ }
  }

  function doFetch(attempt) {
    const cbName = 'bsz_cb_' + attempt + '_' + Date.now()
    window[cbName] = (data) => {
      onApiResult(data)
      try { delete window[cbName] } catch (e) { window[cbName] = undefined }
      const t = document.getElementById('bsz_jsonp_tag')
      if (t) t.remove()
    }

    if (!timer) {
      timer = setTimeout(() => {
        if (display.value === '…') {
          if (attempt < 2) { doFetch(attempt + 1); return }
          display.value = '—'
        }
      }, 8000)
    }

    const s = document.createElement('script')
    s.id = 'bsz_jsonp_tag'
    s.src = 'https://busuanzi.ibruce.info/busuanzi?jsonpCallback=' + cbName + '&_t=' + Date.now()
    document.head.appendChild(s)
  }

  doFetch(1)
}

onMounted(() => {
  // 1. 同会话已计过数 → 直接用缓存
  if (sessionStorage.getItem(COUNTED_KEY)) {
    if (!showCached()) display.value = '—'
    return
  }
  // 2. 1h 冷却期内 → 展示缓存值，不调 API
  try {
    const cachedTs = localStorage.getItem(CACHE_TS_KEY)
    if (cachedTs && Date.now() - parseInt(cachedTs, 10) < COOLDOWN_MS) {
      if (!showCached()) display.value = '—'
      return
    }
  } catch (e) { /* 静默 */ }
  // 3. 需要真正计数 → JSONP 调不蒜子 API
  try { sessionStorage.setItem(COUNTED_KEY, '1') } catch (e) { /* 静默 */ }
  fetchCount()
})
</script>
