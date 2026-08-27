<script setup lang="ts">
import { RefreshCw, Shield, ShieldAlert, ShieldCheck } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { api } from '@/services/api'
import type {
  ContentSecurityAssessment,
  ContentSecurityPolicy,
  ContentSecuritySummary,
  GuardrailDecision,
  SandboxHealth,
  ToolSandboxCapabilities,
} from '@/types/api'

const loading = ref(true)
const error = ref('')
const sandbox = ref<ToolSandboxCapabilities | null>(null)
const sandboxHealth = ref<SandboxHealth | null>(null)
const policy = ref<ContentSecurityPolicy | null>(null)
const summary = ref<ContentSecuritySummary | null>(null)
const assessments = ref<ContentSecurityAssessment[]>([])
const decisions = ref<GuardrailDecision[]>([])
const dispositionFilter = ref('')
const decisionFilter = ref('')

const DISPOSITION_LABELS: Record<string, string> = {
  allowed: '放行',
  denied: '拒绝',
  pending_review: '待审核',
  truncated: '截断',
  isolated: '隔离',
}

const decisionCounts = computed(() => {
  const counts: Record<string, number> = {}
  for (const d of decisions.value) {
    counts[d.decision] = (counts[d.decision] ?? 0) + 1
  }
  return counts
})

function fmt(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [sb, sh, pol, sum, ass, dec] = await Promise.all([
      api.getToolSandboxCapabilities(),
      api.getSandboxHealth(),
      api.getContentSecurityPolicy(),
      api.getContentSecuritySummary(),
      api.listContentSecurityAssessments({ limit: 100 }),
      api.listGuardrailDecisions({ limit: 100 }),
    ])
    sandbox.value = sb
    sandboxHealth.value = sh
    policy.value = pol
    summary.value = sum
    assessments.value = ass
    decisions.value = dec
  } catch (e) {
    error.value = '安全数据加载失败：' + (e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">安全治理：沙箱策略与内容安全</h1>
        <p class="page-subtitle">M15 工具沙箱/网络出口/密钥治理 + M16 不可信内容与注入防御。</p>
      </div>
      <div class="header-actions">
        <button class="btn ghost" :disabled="loading" @click="load"><RefreshCw :size="15" /> 刷新</button>
      </div>
    </header>

    <div v-if="error" class="error-box">{{ error }}</div>

    <div v-if="loading" class="empty-state">加载中…</div>

    <template v-else>
      <!-- 沙箱健康 -->
      <section class="panel">
        <h3 class="panel-title"><ShieldCheck :size="15" /> 工具沙箱（M15）</h3>
        <div v-if="sandboxHealth" class="health-strip">
          <div class="h-item"><span class="h-label">策略模式</span><span class="badge" :class="sandboxHealth.policy_mode">{{ sandboxHealth.policy_mode }}</span></div>
          <div class="h-item"><span class="h-label">容器支持</span><span>{{ sandboxHealth.container_supported ? '是' : '否' }}</span></div>
          <div class="h-item"><span class="h-label">受限执行器</span><span class="muted">{{ sandboxHealth.restricted_executor }}</span></div>
          <div class="h-item note"><span class="h-label">说明</span><span class="muted">{{ sandboxHealth.note }}</span></div>
        </div>
        <table v-if="sandbox" class="table">
          <thead><tr><th>工具</th><th>执行类</th><th>网络模式</th><th>域名白名单</th></tr></thead>
          <tbody>
            <tr v-for="tool in sandbox.tools" :key="tool.name">
              <td class="mono">{{ tool.name }}</td>
              <td><span class="badge" :class="tool.execution_class">{{ tool.execution_class }}</span></td>
              <td>{{ tool.network.mode }}</td>
              <td class="muted">{{ (tool.network.domains || []).slice(0, 4).join(', ') }}{{ (tool.network.domains || []).length > 4 ? '…' : '' }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- 内容安全策略 -->
      <section class="panel">
        <h3 class="panel-title"><ShieldCheck :size="15" /> 内容安全策略（M16）</h3>
        <div v-if="policy" class="policy-grid">
          <div class="p-item"><span class="p-label">模式</span><span class="badge" :class="policy.mode">{{ policy.mode }}</span></div>
          <div class="p-item"><span class="p-label">策略版本</span><span class="mono">{{ policy.policy_version }}</span></div>
          <div class="p-item"><span class="p-label">信任等级</span><span class="muted">{{ policy.trust_levels.join(' / ') }}</span></div>
          <div class="p-item"><span class="p-label">检测器</span><span class="muted">{{ policy.detectors.join(' / ') }}</span></div>
          <div class="p-item wide"><span class="p-label">硬边界</span><span class="muted">{{ policy.hard_boundaries.join('；') }}</span></div>
        </div>
        <div v-if="summary" class="summary-grid">
          <div class="s-item"><span class="s-label">处置分布</span><span class="mono">{{ JSON.stringify(summary.by_disposition) }}</span></div>
          <div class="s-item"><span class="s-label">信任分布</span><span class="mono">{{ JSON.stringify(summary.by_trust_level) }}</span></div>
        </div>
      </section>

      <!-- 评估 -->
      <section class="panel">
        <h3 class="panel-title"><ShieldAlert :size="15" /> 内容评估（{{ assessments.length }}）</h3>
        <table class="table">
          <thead><tr><th>对象</th><th>信任</th><th>分数</th><th>处置</th><th>信号</th><th>原因</th><th>时间</th></tr></thead>
          <tbody>
            <tr v-for="a in assessments" :key="a.id">
              <td class="mono muted">{{ a.object_type }}:{{ a.object_id.slice(0, 10) }}</td>
              <td>{{ a.trust_level }}</td>
              <td>{{ a.score.toFixed(2) }}</td>
              <td><span class="badge" :class="a.disposition">{{ DISPOSITION_LABELS[a.disposition] || a.disposition }}</span></td>
              <td class="muted">{{ (a.risk_signals || []).map((s) => s.name).join(', ') || '—' }}</td>
              <td class="muted">{{ a.reason }}</td>
              <td class="muted">{{ fmt(a.created_at) }}</td>
            </tr>
            <tr v-if="assessments.length === 0"><td colspan="7" class="muted center">暂无内容评估</td></tr>
          </tbody>
        </table>
      </section>

      <!-- 护栏决策 -->
      <section class="panel">
        <h3 class="panel-title"><Shield :size="15" /> 工具护栏决策（{{ decisions.length }}）</h3>
        <div class="decision-strip">
          <span v-for="(count, key) in decisionCounts" :key="key" class="d-chip">{{ key }}: {{ count }}</span>
        </div>
        <table class="table">
          <thead><tr><th>阶段</th><th>工具</th><th>决策</th><th>原因</th><th>策略版本</th><th>时间</th></tr></thead>
          <tbody>
            <tr v-for="d in decisions" :key="d.id">
              <td>{{ d.stage }}</td>
              <td class="mono">{{ d.tool || '—' }}</td>
              <td><span class="badge" :class="d.decision">{{ d.decision }}</span></td>
              <td class="muted">{{ d.reason }}</td>
              <td class="mono muted">{{ d.policy_version }}</td>
              <td class="muted">{{ fmt(d.created_at) }}</td>
            </tr>
            <tr v-if="decisions.length === 0"><td colspan="6" class="muted center">暂无护栏决策</td></tr>
          </tbody>
        </table>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page { padding: 28px 32px 60px; max-width: 1180px; margin: 0 auto; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 22px; }
.page-title { font-size: 24px; font-weight: 700; margin: 0 0 4px; }
.page-subtitle { color: var(--text-muted); margin: 0; font-size: 13px; }
.header-actions { display: flex; gap: 8px; }
.btn { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 7px 14px; font-size: 13px; cursor: pointer; color: var(--text); }
.btn.ghost { background: transparent; }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 16px; }
.panel-title { display: flex; align-items: center; gap: 6px; margin: 0 0 12px; font-size: 14px; font-weight: 600; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--text-muted); }
.badge.enforce, .badge.restricted_process, .badge.allowed, .badge.allow { background: rgba(16, 185, 129, 0.12); color: #047857; }
.badge.audit_only, .badge.container, .badge.pending_review { background: rgba(245, 158, 11, 0.12); color: #b45309; }
.badge.trusted_in_process, .badge.denied, .badge.deny, .badge.block { background: rgba(239, 68, 68, 0.12); color: #b91c1c; }
.badge.truncated, .badge.isolated { background: rgba(124, 108, 246, 0.12); color: #6d28d9; }
.health-strip, .decision-strip { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 12px; }
.h-item { font-size: 13px; }
.h-label { display: block; font-size: 11px; color: var(--text-soft); margin-bottom: 2px; }
.h-item.note { flex: 1; min-width: 200px; }
.d-chip { font-size: 12px; background: var(--surface-strong); border-radius: 999px; padding: 4px 12px; }
.policy-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 12px; }
.p-item { background: var(--surface-muted); border-radius: 8px; padding: 8px 10px; font-size: 13px; }
.p-item.wide { grid-column: span 4; }
.p-label { display: block; font-size: 11px; color: var(--text-soft); margin-bottom: 2px; }
.summary-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.s-item { background: var(--surface-muted); border-radius: 8px; padding: 8px 10px; font-size: 12px; }
.s-label { display: block; font-size: 11px; color: var(--text-soft); margin-bottom: 2px; }
.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th { text-align: left; color: var(--text-muted); font-weight: 600; font-size: 12px; padding: 8px 10px; border-bottom: 1px solid var(--border); }
.table td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
.mono { font-family: ui-monospace, monospace; font-size: 12px; }
.muted { color: var(--text-muted); }
.center { text-align: center; }
.empty-state { text-align: center; color: var(--text-soft); padding: 48px 0; font-size: 14px; }
.error-box { background: rgba(239, 68, 68, 0.08); color: #b91c1c; border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 14px; }
</style>
