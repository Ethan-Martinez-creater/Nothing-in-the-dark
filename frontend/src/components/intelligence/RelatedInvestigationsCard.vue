<script setup lang="ts">
// V3 §43.1：Related Investigations Card（Overview 右侧，最多 5 条）。
// 点击条目跳转对方调查 Overview。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { crossApi, type RelatedInvestigation } from '@/services/api/intelligence'

const props = defineProps<{ caseId: string }>()

const router = useRouter()
const items = ref<RelatedInvestigation[]>([])
const loading = ref(true)
const error = ref('')

const RELATION_LABELS: Record<string, string> = {
  shared_actor: '共享账号',
  shared_post: '共享帖子',
  shared_media: '共享媒体',
  shared_content: '共享内容',
}

const top = computed(() => items.value.slice(0, 5))

function openRelated(caseId: string) {
  router.push(`/investigations/${caseId}/overview`)
}

onMounted(async () => {
  try {
    items.value = await crossApi.related(props.caseId, 5)
  } catch {
    error.value = '加载关联调查失败。'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="relcard" aria-label="关联调查">
    <div class="relcard__head">
      <h3 class="relcard__title">关联调查</h3>
      <span class="relcard__count">{{ items.length > 5 ? '5+' : items.length }}</span>
    </div>

    <p v-if="loading" class="relcard__hint">正在加载…</p>
    <p v-else-if="error" class="relcard__error">{{ error }}</p>
    <p v-else-if="top.length === 0" class="relcard__hint">
      暂无关联调查 — 出现共享账号、帖子、媒体或内容后自动建立。
    </p>

    <ul v-else class="relcard__list">
      <li v-for="item in top" :key="item.case_id">
        <button type="button" class="relcard__item" @click="openRelated(item.case_id)">
          <span class="relcard__item-title">{{ item.title }}</span>
          <span class="relcard__item-meta">
            {{ item.relation_types.map((r) => RELATION_LABELS[r] ?? r).join('、') }}
            · {{ item.relation_count }} 条关联
            <template v-if="item.has_candidate_relation"> · 含候选</template>
          </span>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.relcard {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
}

.relcard__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.relcard__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.relcard__count {
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--surface-strong);
  color: var(--text-soft);
  font-size: 11px;
  font-weight: 700;
}

.relcard__hint {
  margin: 0;
  color: var(--text-muted);
  font-size: 13px;
}

.relcard__error {
  margin: 0;
  color: var(--red);
  font-size: 13px;
}

.relcard__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.relcard__item {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  text-align: left;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-muted);
  cursor: pointer;
}

.relcard__item:hover {
  border-color: var(--accent);
}

.relcard__item-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
}

.relcard__item-meta {
  font-size: 11px;
  color: var(--text-muted);
}
</style>