<script setup lang="ts">
// Optimization V2 (M1.1)：Signals 路由骨架。
// 内容在 M6 完成：全局 Signal Inbox（基于现有 Monitor Alert adapter）。
</script>

<template>
  <div class="signals-view">
    <h1 class="signals-view__title">信号</h1>
    <p class="signals-view__hint">全局信号收件箱正在建设中（M6 交付）。</p>
  </div>
</template>

<style scoped>
.signals-view {
  max-width: 1080px;
  margin: 0 auto;
  padding: 24px;
}

.signals-view__title {
  margin: 0 0 8px;
  font-size: 24px;
  font-weight: 700;
}

.signals-view__hint {
  color: var(--text-muted);
  font-size: 13px;
}
</style>
