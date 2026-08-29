// Optimization V2 (M2.3)：Investigation Shell 共享的 UI 上下文。
// 子页面通过 provide/inject 设置当前工作区与选中对象；Copilot 发送消息时
// snapshot 该上下文（M2.4）。只传结构化 DTO，不把 Vue 对象序列化发送。
import { inject, provide, ref, type InjectionKey, type Ref } from 'vue'

export type InvestigationWorkspace =
  | 'overview'
  | 'live_data'
  | 'evidence'
  | 'network'
  | 'timeline'
  | 'findings'
  | 'report'
  | 'activity'

export interface InvestigationUiContext {
  workspace: InvestigationWorkspace
  selected_type?: string
  selected_id?: string
  selected_label?: string
  filters?: Record<string, unknown>
  time_range?: {
    start?: string
    end?: string
  }
}

export interface InvestigationContextApi {
  uiContext: Ref<InvestigationUiContext>
  /** 进入工作区或更新选中对象（浅合并）。 */
  setUiContext(patch: Partial<InvestigationUiContext>): void
  /** 清除选中对象，但保留 workspace。 */
  clearSelection(): void
}

const KEY: InjectionKey<InvestigationContextApi> = Symbol('investigation-context')

export function provideInvestigationContext(
  initialWorkspace: InvestigationWorkspace = 'overview',
): InvestigationContextApi {
  const uiContext = ref<InvestigationUiContext>({ workspace: initialWorkspace })

  function setUiContext(patch: Partial<InvestigationUiContext>) {
    uiContext.value = { ...uiContext.value, ...patch }
  }

  function clearSelection() {
    uiContext.value = {
      workspace: uiContext.value.workspace,
      filters: uiContext.value.filters,
      time_range: uiContext.value.time_range,
    }
  }

  const api: InvestigationContextApi = { uiContext, setUiContext, clearSelection }
  provide(KEY, api)
  return api
}

export function useInvestigationContext(): InvestigationContextApi {
  const api = inject(KEY)
  if (!api) {
    throw new Error(
      'useInvestigationContext must be used inside InvestigationShellView',
    )
  }
  return api
}

/** Copilot 发送前 snapshot：Run 创建后 context 不随用户切 tab 改变。 */
export function snapshotUiContext(
  context: Ref<InvestigationUiContext>,
): InvestigationUiContext {
  return JSON.parse(JSON.stringify(context.value)) as InvestigationUiContext
}
