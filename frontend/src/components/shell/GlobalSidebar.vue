<script setup lang="ts">
// Optimization V2 (M1.2)：全局一级导航。
// 结构：Brand → Primary nav → Investigations 树（slot）→ footer。
// 管理入口已移至 GlobalTopbar 顶栏悬停菜单，避免挤占调查列表空间。
import { Boxes, MessageSquarePlus } from 'lucide-vue-next'
import { RouterLink, useRoute } from 'vue-router'

const route = useRoute()

const emit = defineEmits<{
  (e: 'new-investigation'): void
  (e: 'open-skills'): void
}>()

const primaryNav = [
  { path: '/', label: '首页', match: (p: string) => p === '/' },
  { path: '/signals', label: '信号', match: (p: string) => p.startsWith('/signals') },
  {
    // V3 §45：一级导航顺序 Home / Signals / Intelligence / Investigations / Reports。
    path: '/intelligence',
    label: '情报',
    match: (p: string) => p.startsWith('/intelligence'),
  },
  {
    path: '/investigations',
    label: '调查',
    match: (p: string) => p.startsWith('/investigations') || p.startsWith('/cases'),
  },
  { path: '/reports', label: '报告', match: (p: string) => p.startsWith('/reports') },
] as const
</script>

<template>
  <div class="gsidebar">
    <div class="gsidebar__brand">
      <div class="gsidebar__brand-mark">C</div>
      <div class="gsidebar__brand-copy">
        <strong>COIFESP</strong>
        <span>Investigation Workbench</span>
      </div>
    </div>

    <nav class="gsidebar__nav" aria-label="主导航">
      <RouterLink
        v-for="item in primaryNav"
        :key="item.path"
        :to="item.path"
        class="gsidebar__nav-item"
        :class="{ 'gsidebar__nav-item--active': item.match(route.path) }"
      >
        {{ item.label }}
      </RouterLink>
    </nav>

    <div class="gsidebar__tools">
      <button type="button" class="gsidebar__tool" @click="emit('new-investigation')">
        <MessageSquarePlus :size="15" />
        <span>新建调查</span>
      </button>
      <div class="gsidebar__tool-row">
        <button type="button" class="gsidebar__tool" @click="emit('open-skills')">
          <Boxes :size="15" />
          <span>技能</span>
        </button>
      </div>
    </div>

    <div class="gsidebar__list">
      <slot />
    </div>

    <div class="gsidebar__footer">
      <slot name="footer" />
    </div>
  </div>
</template>

<style scoped>
.gsidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.gsidebar__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 14px 10px;
}

.gsidebar__brand-mark {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: var(--accent);
  color: #fff;
  font-weight: 700;
}

.gsidebar__brand-copy {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.gsidebar__brand-copy strong {
  font-size: 14px;
}

.gsidebar__brand-copy span {
  font-size: 11px;
  color: var(--text-soft);
}

.gsidebar__nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
}

.gsidebar__nav-item {
  padding: 8px 10px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  text-decoration: none;
  transition:
    background 120ms ease,
    color 120ms ease;
}

.gsidebar__nav-item:hover {
  background: var(--surface-strong);
  color: var(--text);
}

.gsidebar__nav-item--active {
  background: rgba(37, 99, 235, 0.1);
  color: var(--accent-strong);
}

.gsidebar__tools {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
}

.gsidebar__tool-row {
  display: flex;
  gap: 6px;
}

.gsidebar__tool-row .gsidebar__tool {
  flex: 1;
}

.gsidebar__tool {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
}

.gsidebar__tool:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.gsidebar__list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 10px;
}

.gsidebar__footer {
  padding: 8px 12px 12px;
}
</style>
