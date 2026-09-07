<script setup lang="ts">
// FindingDebateModal（M5.2/M5.6）：Finding Detail 的对抗性审查弹窗。
// 选择 Modal 而非第二个 Drawer（InvestigationShell 已有 Copilot Drawer）。
// 内嵌 DebatePanel（debateId 隔离加载）；历史 completed 只读，可发起新一轮。
import { computed, ref, watch } from 'vue'
import { Gavel, Loader2, Plus, X } from 'lucide-vue-next'

import { api } from '@/services/api'
import type { Debate } from '@/types/api'
import DebatePanel from './DebatePanel.vue'

const props = defineProps<{
  caseId: string
  findingId: string
  findingTitle: string
  findingStatus: string
  debateId: string | null
}>()

const emit = defineEmits<{ close: []; completed: [] }>()

const history = ref<Debate[]>([])
const activeId = ref<string | null>(props.debateId)
const loadingHistory = ref(false)
const creating = ref(false)
const error = ref('')

watch(
  () => props.debateId,
  (value) => {
    if (value) activeId.value = value
  },
)

async function loadHistory() {
  loadingHistory.value = true
  error.value = ''
  try {
    history.value = await api.listFindingDebates(props.caseId, props.findingId)
  } catch {
    error.value = '挑战历史加载失败。'
  } finally {
    loadingHistory.value = false
  }
}

watch(
  () => props.findingId,
  () => {
    activeId.value = props.debateId
    void loadHistory()
  },
  { immediate: true },
)

async function viewHistory(debate: Debate) {
  activeId.value = debate.id
}

async function startNewChallenge() {
  if (creating.value) return
  creating.value = true
  error.value = ''
  try {
    const debate = await api.createFindingDebate(props.caseId, props.findingId)
    activeId.value = debate.id
    await loadHistory()
  } catch {
    error.value = '发起挑战失败，请稍后重试。'
  } finally {
    creating.value = false
  }
}

function onPanelCompleted() {
  emit('completed')
  void loadHistory()
}

const statusLabel = (debate: Debate) =>
  debate.status === 'completed' ? '已完成' : '进行中'

const timeLabel = (iso: string) => iso.replace('T', ' ').slice(0, 16)

const hasHistory = computed(() => history.value.length > 0)
</script>

<template>
  <div class="fdm__overlay" @click.self="emit('close')">
    <div class="fdm" role="dialog" aria-modal="true" aria-label="对抗性审查">
      <header class="fdm__head">
        <h3 class="fdm__title"><Gavel :size="16" /> 对抗性审查</h3>
        <div class="fdm__meta">
          <span class="fdm__finding-title">{{ findingTitle }}</span>
          <span class="fdm__finding-status">{{ findingStatus }}</span>
        </div>
        <button
          type="button"
          class="fdm__close"
          aria-label="关闭"
          @click="emit('close')"
        >
          <X :size="16" />
        </button>
      </header>

      <p v-if="error" class="fdm__error">{{ error }}</p>

      <div class="fdm__body">
        <DebatePanel
          :key="activeId ?? 'none'"
          v-if="activeId"
          :case-id="caseId"
          :debate-id="activeId"
          embedded
          @completed="onPanelCompleted"
        />
        <div v-else class="fdm__empty">
          <p>尚未进行对抗性审查。</p>
          <button
            type="button"
            class="fdm__primary"
            :disabled="creating"
            @click="startNewChallenge"
          >
            <Loader2 v-if="creating" class="spin" :size="14" />
            <Plus v-else :size="14" />
            开始挑战
          </button>
        </div>
      </div>

      <footer class="fdm__foot">
        <div class="fdm__history">
          <span class="fdm__history-title">
            历史挑战{{ hasHistory ? ` ${history.length} 次` : '' }}
          </span>
          <p v-if="loadingHistory" class="fdm__hint">加载中…</p>
          <p v-else-if="!hasHistory" class="fdm__hint">尚无挑战记录。</p>
          <ul v-else class="fdm__history-list">
            <li
              v-for="debate in history"
              :key="debate.id"
              class="fdm__history-item"
              :class="{ 'fdm__history-item--active': debate.id === activeId }"
            >
              <button
                type="button"
                class="fdm__history-btn"
                @click="viewHistory(debate)"
              >
                <span>{{ timeLabel(debate.created_at) }}</span>
                <span>{{ statusLabel(debate) }}</span>
                <span>第 {{ debate.round }} 轮 / 4</span>
              </button>
            </li>
          </ul>
        </div>
        <button
          type="button"
          class="fdm__primary"
          :disabled="creating"
          @click="startNewChallenge"
        >
          <Loader2 v-if="creating" class="spin" :size="14" />
          <Plus v-else :size="14" />
          发起新一轮挑战
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.fdm__overlay {
  position: fixed;
  inset: 0;
  z-index: 70;
  background: rgba(15, 23, 42, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.fdm {
  width: 100%;
  max-width: 960px;
  height: 85vh;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

@media (max-width: 720px) {
  .fdm__overlay {
    padding: 0;
  }

  .fdm {
    max-width: none;
    height: 100vh;
    border-radius: 0;
    border: none;
  }
}

.fdm__head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
}

.fdm__title {
  margin: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 700;
}

.fdm__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.fdm__finding-title {
  font-size: 12px;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fdm__finding-status {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--surface-strong);
  color: var(--text-muted);
  flex-shrink: 0;
}

.fdm__close {
  border: none;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  padding: 4px;
}

.fdm__error {
  margin: 0;
  padding: 8px 18px 0;
  color: var(--red);
  font-size: 12px;
}

.fdm__body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.fdm__empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--text-muted);
  font-size: 13px;
}

.fdm__primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border: 1px solid var(--accent);
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}

.fdm__primary:disabled {
  opacity: 0.6;
  cursor: default;
}

.fdm__foot {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 18px 14px;
  border-top: 1px solid var(--border);
}

.fdm__history {
  min-width: 0;
  flex: 1;
}

.fdm__history-title {
  font-size: 11px;
  color: var(--text-muted);
  font-weight: 600;
}

.fdm__hint {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--text-muted);
}

.fdm__history-list {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 88px;
  overflow-y: auto;
}

.fdm__history-btn {
  display: flex;
  gap: 12px;
  width: 100%;
  text-align: left;
  font-size: 11px;
  color: var(--text-muted);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 5px 10px;
  cursor: pointer;
}

.fdm__history-item--active .fdm__history-btn {
  border-color: var(--accent);
  color: var(--accent);
}

.spin {
  animation: fdm-spin 1s linear infinite;
}

@keyframes fdm-spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}
</style>
