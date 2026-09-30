<script setup lang="ts">
// 侧边栏拖拽调宽手柄：拖动改变 --vp-sidebar-width，宽度持久化到 localStorage
import { ref, watch, onMounted, nextTick } from 'vue'
import { useRoute } from 'vitepress'

const KEY = 'vp-sidebar-width'
const MIN = 200
const MAX = 480
const clamp = (w: number) => Math.min(MAX, Math.max(MIN, w))

const visible = ref(false)
const route = useRoute()

function applyWidth(px: number) {
  document.documentElement.style.setProperty('--vp-sidebar-width', `${px}px`)
}

function refreshVisibility() {
  // 仅当当前布局确实展示侧边栏时显示手柄（如首页 hero 布局无侧边栏则隐藏）
  visible.value = document.querySelector('.VPLayout')?.classList.contains('has-sidebar') ?? false
}

function startDrag(e: MouseEvent) {
  e.preventDefault()
  const startX = e.clientX
  const current = parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue('--vp-sidebar-width'),
  ) || 272
  document.body.classList.add('sidebar-resizing')
  const onMove = (ev: MouseEvent) => applyWidth(clamp(current + (ev.clientX - startX)))
  const onUp = (ev: MouseEvent) => {
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
    document.body.classList.remove('sidebar-resizing')
    localStorage.setItem(KEY, String(clamp(current + (ev.clientX - startX))))
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

onMounted(() => {
  // head 内联脚本已预恢复宽度（防闪烁），这里兜底再读一次
  const saved = parseFloat(localStorage.getItem(KEY) || '')
  if (!Number.isNaN(saved)) applyWidth(clamp(saved))
  nextTick(refreshVisibility)
})

// SPA 路由切换后重新判断当前页是否有侧边栏
watch(() => route.path, () => nextTick(refreshVisibility))
</script>

<template>
  <div
    v-show="visible"
    class="vp-sidebar-resizer"
    title="拖动调整侧边栏宽度"
    aria-hidden="true"
    @mousedown="startDrag"
  ></div>
</template>
