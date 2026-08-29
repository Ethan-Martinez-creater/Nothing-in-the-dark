<script setup lang="ts">
// Optimization V2 (M2.3)：通用调查工作区占位。
// M3-M7 按里程碑逐个替换为完整工作区（Overview/Live Data/Evidence/...）。
defineProps<{
  title: string
  milestone: string
}>()
</script>

<template>
  <div class="iplaceholder">
    <h2 class="iplaceholder__title">{{ title }}</h2>
    <p class="iplaceholder__hint">该工作区将在 {{ milestone }} 交付。</p>
  </div>
</template>

<style scoped>
.iplaceholder {
  max-width: 720px;
  margin: 0 auto;
  padding: 48px 24px;
  text-align: center;
}

.iplaceholder__title {
  margin: 0 0 8px;
  font-size: 18px;
  font-weight: 600;
  color: var(--text);
}

.iplaceholder__hint {
  margin: 0;
  font-size: 13px;
  color: var(--text-muted);
}
</style>
