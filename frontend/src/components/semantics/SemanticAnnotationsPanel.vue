<script setup lang="ts">
// C9.1: 语义标注面板（自 SemanticAnnotationsView 抽出；case 由 workspace 提供）。
// 三个子区：语义标注表 / 版本化词典（新增进入人工审核）/ 在线分析。
import { BookOpenText, ScanText, Send } from 'lucide-vue-next'
import { onMounted, ref, watch } from 'vue'

import { api } from '@/services/api'
import type { LexiconEntry, SemanticAnalysis, SemanticAnnotation } from '@/types/api'

const props = defineProps<{ caseId: string }>()

const loading = ref(false)
const error = ref('')
const notice = ref('')
const annotations = ref<SemanticAnnotation[]>([])
const lexicon = ref<LexiconEntry[]>([])
const analysis = ref<SemanticAnalysis | null>(null)
const activeTab = ref<'annotations' | 'lexicon' | 'analyze'>('annotations')

const newTerm = ref('')
const newMeaning = ref('')
const newDomain = ref('general')
const analyzeText = ref('')
const busy = ref(false)

const TASK_LABELS: Record<string, string> = {
  entity: '实体',
  claim: '主张',
  stance: '立场',
  sentiment: '情感',
  time_reference: '时间引用',
  uncertainty: '不确定性',
}

function fmt(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

async function load() {
  if (!props.caseId) return
  loading.value = true
  error.value = ''
  try {
    const [ann, lex] = await Promise.all([
      api.listSemanticAnnotations(props.caseId),
      api.listLexicon(props.caseId),
    ])
    annotations.value = ann
    lexicon.value = lex
  } catch (e) {
    error.value = '语义数据加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

async function addEntry() {
  if (!newTerm.value.trim()) return
  busy.value = true
  try {
    await api.addLexiconEntry(props.caseId, {
      term: newTerm.value.trim(),
      meaning: newMeaning.value || undefined,
      domain: newDomain.value,
    })
    notice.value = '词典条目已提交（待审核：' + newTerm.value + '）'
    newTerm.value = ''
    newMeaning.value = ''
    lexicon.value = await api.listLexicon(props.caseId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function runAnalysis() {
  if (!analyzeText.value.trim()) return
  busy.value = true
  analysis.value = null
  try {
    analysis.value = await api.analyzeSemantics(props.caseId, {
      text: analyzeText.value.trim(),
      domain: newDomain.value,
    })
    notice.value = '分析完成（语义版本 ' + analysis.value.semantic_version + '）'
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

watch(
  () => props.caseId,
  () => {
    void load()
  },
)
onMounted(load)
</script>

<template>
  <div class="sap">
    <div v-if="error" class="sap__error">{{ error }}</div>
    <div v-if="notice" class="sap__notice">{{ notice }}</div>

    <nav class="sap__tabs">
      <button
        class="sap__tab"
        :class="{ 'sap__tab--active': activeTab === 'annotations' }"
        @click="activeTab = 'annotations'"
      >
        语义标注
      </button>
      <button
        class="sap__tab"
        :class="{ 'sap__tab--active': activeTab === 'lexicon' }"
        @click="activeTab = 'lexicon'"
      >
        词典
      </button>
      <button
        class="sap__tab"
        :class="{ 'sap__tab--active': activeTab === 'analyze' }"
        @click="activeTab = 'analyze'"
      >
        在线分析
      </button>
    </nav>

    <div v-if="loading" class="sap__state">加载中…</div>

    <!-- 标注 -->
    <section v-else-if="activeTab === 'annotations'" class="sap__panel">
      <table class="sap__table">
        <thead>
          <tr><th>来源</th><th>任务</th><th>标签</th><th>跨度</th><th>置信</th><th>提供者</th><th>时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="a in annotations" :key="a.id">
            <td class="sap__mono sap__muted">{{ a.source_id }}</td>
            <td>{{ TASK_LABELS[a.task] || a.task }}</td>
            <td>{{ a.label }}</td>
            <td class="sap__mono">{{ a.span ? '[' + a.span[0] + ',' + a.span[1] + ')' : '—' }}</td>
            <td>{{ (a.confidence * 100).toFixed(0) }}%</td>
            <td class="sap__muted">{{ a.provider }} {{ a.model_version }}</td>
            <td class="sap__muted">{{ fmt(a.created_at) }}</td>
          </tr>
          <tr v-if="annotations.length === 0">
            <td colspan="7" class="sap__muted sap__center">暂无语义标注</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 词典 -->
    <section v-else-if="activeTab === 'lexicon'" class="sap__panel">
      <h3 class="sap__panel-title"><BookOpenText :size="15" /> 新增词典条目（提交后进入人工审核）</h3>
      <div class="sap__row">
        <input v-model="newTerm" class="sap__input" placeholder="术语 / 谐音 / 拆字…" />
        <input v-model="newMeaning" class="sap__input sap__input--wide" placeholder="含义（偏离字面义时必填）" />
        <select v-model="newDomain" class="sap__input">
          <option value="general">通用</option>
          <option value="spam">营销</option>
          <option value="coordinated">协同</option>
        </select>
        <button
          class="sap__btn sap__btn--primary"
          :disabled="!newTerm.trim() || busy"
          @click="addEntry"
        >
          <Send :size="14" /> 提交
        </button>
      </div>
      <table class="sap__table">
        <thead><tr><th>术语</th><th>规范化</th><th>含义</th><th>领域</th><th>审核</th><th>版本</th></tr></thead>
        <tbody>
          <tr v-for="entry in lexicon" :key="entry.id">
            <td>{{ entry.term }}</td>
            <td class="sap__mono sap__muted">{{ entry.normalized }}</td>
            <td class="sap__muted">{{ entry.meaning }}</td>
            <td>{{ entry.domain }}</td>
            <td><span class="sap__badge">{{ entry.review_state }}</span></td>
            <td class="sap__muted">{{ entry.version }}</td>
          </tr>
          <tr v-if="lexicon.length === 0">
            <td colspan="6" class="sap__muted sap__center">暂无词典条目</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 分析 -->
    <section v-else-if="activeTab === 'analyze'" class="sap__panel">
      <h3 class="sap__panel-title"><ScanText :size="15" /> 在线语义分析</h3>
      <div class="sap__row">
        <textarea v-model="analyzeText" class="sap__input sap__input--wide" rows="3" placeholder="输入文本…" />
      </div>
      <div class="sap__row">
        <button class="sap__btn sap__btn--primary" :disabled="!analyzeText.trim() || busy" @click="runAnalysis">
          分析
        </button>
      </div>
      <div v-if="analysis" class="sap__analysis">
        <p class="sap__analysis-orig">{{ analysis.original }}</p>
        <p class="sap__analysis-norm">规范化：{{ analysis.normalized }}</p>
        <p class="sap__analysis-meta">
          语言：{{ analysis.language.language }}{{ analysis.language.mixed ? '（混合）' : '' }} · 回退：{{ analysis.fallback ? '是' : '否' }}
        </p>
        <div v-if="analysis.lexicon_hits.length" class="sap__hits">
          <span v-for="hit in analysis.lexicon_hits" :key="hit.term" class="sap__hit">{{ hit.term }}：{{ hit.meaning }}</span>
        </div>
        <table class="sap__table">
          <thead><tr><th>任务</th><th>标签</th><th>置信</th><th>提供者</th><th>不确定</th></tr></thead>
          <tbody>
            <tr v-for="(res, i) in analysis.results" :key="i">
              <td>{{ res.task }}</td>
              <td>{{ res.label }}</td>
              <td>{{ (res.confidence * 100).toFixed(0) }}%</td>
              <td class="sap__muted">{{ res.provider }}</td>
              <td>{{ res.uncertain ? '是' : '否' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.sap {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sap__error {
  background: rgba(239, 68, 68, 0.08);
  color: #b91c1c;
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 12px;
}

.sap__notice {
  background: rgba(16, 185, 129, 0.1);
  color: #047857;
  border: 1px solid rgba(16, 185, 129, 0.3);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 12px;
}

.sap__tabs {
  display: flex;
  gap: 6px;
}

.sap__tab {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  padding: 6px 12px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text-muted);
}

.sap__tab--active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.sap__panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px;
}

.sap__panel-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 12px;
  font-size: 13px;
  font-weight: 600;
}

.sap__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.sap__table th {
  text-align: left;
  color: var(--text-muted);
  font-weight: 600;
  font-size: 11px;
  padding: 7px 8px;
  border-bottom: 1px solid var(--border);
}

.sap__table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--border);
}

.sap__badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--border);
  color: var(--text-muted);
}

.sap__muted { color: var(--text-muted); }
.sap__mono { font-family: ui-monospace, monospace; font-size: 11px; }
.sap__center { text-align: center; }

.sap__row {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.sap__input {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  background: var(--surface);
  color: var(--text);
  font-family: inherit;
}

.sap__input--wide {
  flex: 1;
  min-width: 200px;
}

.sap__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  padding: 6px 12px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text);
}

.sap__btn--primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.sap__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.sap__analysis { margin-top: 10px; }
.sap__analysis-orig { font-size: 12px; }
.sap__analysis-norm { font-size: 12px; color: var(--cyan-strong); }
.sap__analysis-meta { font-size: 11px; color: var(--text-muted); }

.sap__hits {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin: 8px 0;
}

.sap__hit {
  font-size: 11px;
  background: rgba(124, 108, 246, 0.1);
  color: #6d28d9;
  border-radius: 999px;
  padding: 3px 10px;
}
</style>
