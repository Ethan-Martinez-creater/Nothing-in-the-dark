<script setup lang="ts">
// Optimization V2 (M1.1)：Reports 路由骨架。
// 内容在 M7 完成：全局报告列表（draft/in_review/published/archived）。
</script>

<template>
  <div class="reports-view">
    <h1 class="reports-view__title">报告</h1>
    <p class="reports-view__hint">全局报告中心正在建设中（M7 交付）。</p>
  </div>
</template>

<style scoped>
.reports-view {
  max-width: 1080px;
  margin: 0 auto;
  padding: 24px;
}

.reports-view__title {
  margin: 0 0 8px;
  font-size: 24px;
  font-weight: 700;
}

.reports-view__hint {
  color: var(--text-muted);
  font-size: 13px;
}
</style>
