<script setup lang="ts">
// C8.1: Evidence workspace 右侧详情面板。
// claim 模式：全文 + support/oppose/context 分组 + review action。
// evidence 模式：摘录 + source metadata + provenance downstream
// findings（复用 GET /cases/{id}/provenance/evidence/{id}）。
import { FileText, Sparkles } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'

import { api } from '@/services/api'
import type {
  ClaimEvidence,
  EvidenceItem,
  ProvenanceRefDTO,
} from '@/types/api'

const props = defineProps<{
  caseId: string
  claim: ClaimEvidence | null
  item: EvidenceItem | null
}>()

const emit = defineEmits<{ reviewed: [] }>()

const STANCE_LABELS: Record<string, string> = {
  support: '支持',
  oppose: '反驳',
  context: '背景',
}

const PLATFORM_LABELS: Record<string, string> = {
  weibo: '微博',
  bilibili: '哔哩哔哩',
  tieba: '百度贴吧',
  zhihu: '知乎',
  douyin: '抖音',
}

const reviewing = ref(false)
const reviewError = ref('')
const localStatus = ref('')

const grouped = computed(() => {
  const claim = props.claim
  if (!claim) return null
  return {
    support: claim.evidence.filter((entry) => entry.stance === 'support'),
    oppose: claim.evidence.filter((entry) => entry.stance === 'oppose'),
    context: claim.evidence.filter((entry) => entry.stance === 'context'),
  }
})

const sourceMeta = computed<Record<string, unknown>>(() => {
  return (props.item?.metadata_json as Record<string, unknown>) ?? {}
})

function sourcePlatform(): string {
  const platform = String(sourceMeta.value.platform || '')
  return PLATFORM_LABELS[platform] || platform
}

const relatedFindings = ref<ProvenanceRefDTO[]>([])
const relatedLoading = ref(false)

watch(
  () => props.item?.id,
  async (evidenceId) => {
    relatedFindings.value = []
    if (!evidenceId) return
    relatedLoading.value = true
    try {
      const provenance = await api.getEvidenceProvenance(props.caseId, evidenceId)
      relatedFindings.value = provenance.downstream.filter(
        (ref) => ref.type === 'finding',
      )
    } catch {
      relatedFindings.value = []
    } finally {
      relatedLoading.value = false
    }
  },
  { immediate: true },
)

function stanceLabel(stance: string): string {
  return STANCE_LABELS[stance] || stance
}

async function reviewClaim(confirmed: boolean) {
  const claim = props.claim
  if (!claim || reviewing.value) return
  reviewing.value = true
  reviewError.value = ''
  try {
    const updated = await api.reviewClaim(props.caseId, claim.id, confirmed)
    localStatus.value = updated.status
    emit('reviewed')
  } catch {
    reviewError.value = '人工复核提交失败，请重试。'
  } finally {
    reviewing.value = false
  }
}
</script>

<template>
  <aside class="edp" aria-label="证据详情">
    <p v-if="!claim && !item" class="edp__empty">
      在左侧选择主张或证据查看全文、来源与关联结论。
    </p>

    <!-- Evidence detail -->
    <template v-else-if="item">
      <h3 class="edp__title">证据详情</h3>
      <p class="edp__excerpt">{{ item.excerpt }}</p>
      <dl class="edp__fields">
        <div><dt>立场</dt><dd>{{ stanceLabel(item.stance) }}</dd></div>
        <div><dt>来源类型</dt><dd>{{ item.source_type }}</dd></div>
        <div v-if="sourcePlatform()"><dt>平台</dt><dd>{{ sourcePlatform() }}</dd></div>
        <div v-if="String(sourceMeta.author || '')">
          <dt>作者</dt><dd>{{ String(sourceMeta.author) }}</dd>
        </div>
        <div><dt>相关度</dt><dd>{{ item.relevance.toFixed(2) }}</dd></div>
        <div v-if="String(sourceMeta.url || sourceMeta.source_url || '')">
          <dt>原文链接</dt>
          <dd>
            <a
              :href="String(sourceMeta.url || sourceMeta.source_url)"
              target="_blank"
              rel="noreferrer"
            >打开来源</a>
          </dd>
        </div>
      </dl>
      <div class="edp__related">
        <span class="edp__label">关联结论（Findings）</span>
        <p v-if="relatedLoading" class="edp__muted">查询中…</p>
        <ul v-else-if="relatedFindings.length" class="edp__findings">
          <li v-for="finding in relatedFindings" :key="finding.id">
            {{ finding.label || finding.id }}
            <em v-if="finding.relation">{{ finding.relation }}</em>
          </li>
        </ul>
        <p v-else class="edp__muted">暂无引用该证据的 Finding</p>
      </div>
    </template>

    <!-- Claim detail -->
    <template v-else-if="claim">
      <h3 class="edp__title">主张详情</h3>
      <p class="edp__claim-text">{{ claim.text }}</p>
      <div class="edp__actions">
        <button
          type="button"
          class="ghost-button"
          :disabled="reviewing"
          @click="reviewClaim(true)"
        >
          确认主张
        </button>
        <button
          type="button"
          class="ghost-button danger"
          :disabled="reviewing"
          @click="reviewClaim(false)"
        >
          驳回主张
        </button>
      </div>
      <p v-if="reviewError" class="edp__error">{{ reviewError }}</p>
      <template v-if="grouped">
        <div v-for="(entries, stance) in grouped" :key="stance" class="edp__stance">
          <span class="edp__label">{{ stanceLabel(String(stance)) }}（{{ entries.length }}）</span>
          <ul v-if="entries.length" class="edp__findings">
            <li v-for="entry in entries" :key="entry.id">{{ entry.excerpt }}</li>
          </ul>
          <p v-else class="edp__muted">无</p>
        </div>
      </template>
    </template>

    <div v-if="!claim && !item" class="edp__guide">
      <FileText :size="18" />
      <p>证据随分析产生：发起分析后这里会展示按主张组织的证据。</p>
      <Sparkles :size="14" />
    </div>
  </aside>
</template>

<style scoped>
.edp {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border-left: 1px solid var(--border);
  overflow-y: auto;
}

.edp__empty {
  margin: auto;
  color: var(--text-soft);
  font-size: 12px;
}

.edp__title {
  margin: 0;
  font-size: 14px;
}

.edp__excerpt,
.edp__claim-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
}

.edp__fields {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.edp__fields div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}

.edp__fields dt {
  color: var(--text-soft);
}

.edp__fields dd {
  margin: 0;
  color: var(--text);
  word-break: break-all;
}

.edp__label {
  font-size: 11px;
  color: var(--text-soft);
}

.edp__findings {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
}

.edp__findings em {
  color: var(--text-soft);
  font-size: 11px;
}

.edp__muted {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-soft);
}

.edp__error {
  margin: 0;
  color: var(--red);
  font-size: 12px;
}

.edp__actions {
  display: flex;
  gap: 8px;
}

.edp__stance {
  display: flex;
  flex-direction: column;
}

.edp__guide {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
  color: var(--text-soft);
  font-size: 12px;
}
</style>
