<script setup lang="ts">
import { BookOpenText, RefreshCw, ScanText, Send } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type { CaseRecord, LexiconEntry, SemanticAnalysis, SemanticAnnotation } from '@/types/api'

const loading = ref(true)
const error = ref('')
const notice = ref('')
const cases = ref<CaseRecord[]>([])
const selectedCaseId = ref('')
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
  loading.value = true
  error.value = ''
  try {
    cases.value = await api.listCases()
  } catch (e) {
    error.value = '案件加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

async function selectCase() {
  if (!selectedCaseId.value) return
  loading.value = true
  error.value = ''
  try {
    const [ann, lex] = await Promise.all([
      api.listSemanticAnnotations(selectedCaseId.value),
      api.listLexicon(selectedCaseId.value),
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
    await api.addLexiconEntry(selectedCaseId.value, {
      term: newTerm.value.trim(),
      meaning: newMeaning.value || undefined,
      domain: newDomain.value,
    })
    notice.value = '词典条目已提交（待审核：' + newTerm.value + '）'
    newTerm.value = ''
    newMeaning.value = ''
    lexicon.value = await api.listLexicon(selectedCaseId.value)
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
    analysis.value = await api.analyzeSemantics(selectedCaseId.value, {
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

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">中文复杂语义与跨语言分析</h1>
        <p class="page-subtitle">M11：语义标注查看与人工纠错、版本化词典、在线分析。</p>
      </div>
      <div class="header-actions">
        <button class="btn ghost" :disabled="loading" @click="load"><RefreshCw :size="15" /> 刷新</button>
      </div>
    </header>

    <div v-if="error" class="error-box">{{ error }}</div>
    <div v-if="notice" class="notice">{{ notice }}</div>

    <div class="toolbar">
      <select v-model="selectedCaseId" class="filter-select" @change="selectCase">
        <option value="">选择案件…</option>
        <option v-for="c in cases" :key="c.id" :value="c.id">{{ c.title }}</option>
      </select>
    </div>

    <nav class="tabs">
      <button class="tab" :class="{ active: activeTab === 'annotations' }" @click="activeTab = 'annotations'">语义标注</button>
      <button class="tab" :class="{ active: activeTab === 'lexicon' }" @click="activeTab = 'lexicon'">词典</button>
      <button class="tab" :class="{ active: activeTab === 'analyze' }" @click="activeTab = 'analyze'">在线分析</button>
    </nav>

    <div v-if="loading" class="empty-state">加载中…</div>
    <div v-else-if="!selectedCaseId" class="empty-state">请选择案件。</div>

    <!-- 标注 -->
    <template v-if="activeTab === 'annotations'">
      <section class="panel">
        <table class="table">
          <thead><tr><th>来源</th><th>任务</th><th>标签</th><th>跨度</th><th>置信</th><th>提供者</th><th>时间</th></tr></thead>
          <tbody>
            <tr v-for="a in annotations" :key="a.id">
              <td class="mono muted">{{ a.source_id }}</td>
              <td>{{ TASK_LABELS[a.task] || a.task }}</td>
              <td>{{ a.label }}</td>
              <td class="mono">{{ a.span ? '[' + a.span[0] + ',' + a.span[1] + ')' : '—' }}</td>
              <td>{{ (a.confidence * 100).toFixed(0) }}%</td>
              <td class="muted">{{ a.provider }} {{ a.model_version }}</td>
              <td class="muted">{{ fmt(a.created_at) }}</td>
            </tr>
            <tr v-if="annotations.length === 0"><td colspan="7" class="muted center">暂无语义标注</td></tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- 词典 -->
    <template v-if="activeTab === 'lexicon'">
      <section class="panel">
        <h3 class="panel-title"><BookOpenText :size="15" /> 新增词典条目（提交后进入人工审核）</h3>
        <div class="create-row">
          <input v-model="newTerm" class="text-input" placeholder="术语 / 谐音 / 拆字…" />
          <input v-model="newMeaning" class="text-input wide" placeholder="含义（偏离字面义时必填）" />
          <select v-model="newDomain" class="filter-select">
            <option value="general">通用</option>
            <option value="spam">营销</option>
            <option value="coordinated">协同</option>
          </select>
          <button class="btn primary small" :disabled="!newTerm.trim() || busy" @click="addEntry"><Send :size="14" /> 提交</button>
        </div>
        <table class="table">
          <thead><tr><th>术语</th><th>规范化</th><th>含义</th><th>领域</th><th>审核</th><th>版本</th></tr></thead>
          <tbody>
            <tr v-for="entry in lexicon" :key="entry.id">
              <td>{{ entry.term }}</td>
              <td class="mono muted">{{ entry.normalized }}</td>
              <td class="muted">{{ entry.meaning }}</td>
              <td>{{ entry.domain }}</td>
              <td><span class="badge" :class="entry.review_state">{{ entry.review_state }}</span></td>
              <td class="muted">{{ entry.version }}</td>
            </tr>
            <tr v-if="lexicon.length === 0"><td colspan="6" class="muted center">暂无词典条目</td></tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- 分析 -->
    <template v-if="activeTab === 'analyze'">
      <section class="panel">
        <h3 class="panel-title"><ScanText :size="15" /> 在线语义分析</h3>
        <div class="create-row">
          <textarea v-model="analyzeText" class="text-input wide" rows="3" placeholder="输入文本…" />
        </div>
        <div class="create-row">
          <button class="btn primary small" :disabled="!analyzeText.trim() || busy" @click="runAnalysis">分析</button>
        </div>
        <div v-if="analysis" class="analysis-box">
          <p class="analysis-orig">{{ analysis.original }}</p>
          <p class="analysis-norm">规范化：{{ analysis.normalized }}</p>
          <p class="analysis-meta">语言：{{ analysis.language.language }}{{ analysis.language.mixed ? '（混合）' : '' }} · 回退：{{ analysis.fallback ? '是' : '否' }}</p>
          <div v-if="analysis.lexicon_hits.length" class="lexicon-hits">
            <span v-for="hit in analysis.lexicon_hits" :key="hit.term" class="hit-chip">{{ hit.term }}：{{ hit.meaning }}</span>
          </div>
          <table class="table">
            <thead><tr><th>任务</th><th>标签</th><th>置信</th><th>提供者</th><th>不确定</th></tr></thead>
            <tbody>
              <tr v-for="(res, i) in analysis.results" :key="i">
                <td>{{ res.task }}</td>
                <td>{{ res.label }}</td>
                <td>{{ (res.confidence * 100).toFixed(0) }}%</td>
                <td class="muted">{{ res.provider }}</td>
                <td>{{ res.uncertain ? '是' : '否' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page { padding: 28px 32px 60px; max-width: 1080px; margin: 0 auto; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 22px; }
.page-title { font-size: 24px; font-weight: 700; margin: 0 0 4px; }
.page-subtitle { color: var(--text-muted); margin: 0; font-size: 13px; }
.header-actions { display: flex; gap: 8px; }
.btn {
  display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--surface); padding: 7px 14px; font-size: 13px; cursor: pointer; color: var(--text);
}
.btn.primary { background: var(--cyan); border-color: var(--cyan); color: #fff; }
.btn.ghost { background: transparent; }
.btn.small { padding: 4px 9px; font-size: 12px; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.error-box { background: rgba(239, 68, 68, 0.08); color: #b91c1c; border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.notice { background: rgba(16, 185, 129, 0.1); color: #047857; border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.filter-select { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 7px 10px; font-size: 13px; color: var(--text); max-width: 340px; }
.text-input { border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px; font-size: 13px; background: var(--surface); color: var(--text); font-family: inherit; }
.text-input.wide { width: 100%; min-width: 200px; }
.tabs { display: flex; gap: 8px; margin-bottom: 16px; }
.tab { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 7px 14px; font-size: 13px; cursor: pointer; color: var(--text-muted); }
.tab.active { background: var(--cyan); border-color: var(--cyan); color: #fff; }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 16px; }
.panel-title { display: flex; align-items: center; gap: 6px; margin: 0 0 12px; font-size: 14px; font-weight: 600; }
.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th { text-align: left; color: var(--text-muted); font-weight: 600; font-size: 12px; padding: 8px 10px; border-bottom: 1px solid var(--border); }
.table td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--text-muted); }
.badge.approved, .badge.accepted { background: rgba(16, 185, 129, 0.12); color: #047857; }
.badge.proposed, .badge.pending_review { background: rgba(245, 158, 11, 0.12); color: #b45309; }
.badge.rejected { background: rgba(239, 68, 68, 0.12); color: #b91c1c; }
.muted { color: var(--text-muted); }
.mono { font-family: ui-monospace, monospace; font-size: 12px; }
.center { text-align: center; }
.create-row { display: flex; gap: 8px; margin-bottom: 12px; align-items: center; flex-wrap: wrap; }
.analysis-box { margin-top: 10px; }
.analysis-orig { font-size: 13px; }
.analysis-norm { font-size: 13px; color: var(--cyan-strong); }
.analysis-meta { font-size: 12px; color: var(--text-muted); }
.lexicon-hits { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
.hit-chip { font-size: 12px; background: rgba(124, 108, 246, 0.1); color: #6d28d9; border-radius: 999px; padding: 3px 10px; }
.empty-state { text-align: center; color: var(--text-soft); padding: 48px 0; font-size: 14px; }
</style>
