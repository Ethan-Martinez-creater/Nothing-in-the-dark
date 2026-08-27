export interface CaseRecord {
  id: string
  title: string
  topic: string
  description: string
  status: 'draft' | 'ready' | 'running' | 'completed' | 'failed' | 'archived'
  platforms: string[]
  time_range: {
    start: string | null
    end: string | null
  }
  project_id: string | null
  created_at: string
  updated_at: string
}

export interface Project {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface SystemCapabilities {
  version: string
  environment: string
  demo_mode: boolean
  framework: string
  production_entry?: 'messages'
  legacy_analysis?: boolean
  durable_checkpointer?: 'postgresql' | 'memory'
  llm_configured?: boolean
  platforms: string[]
  llm: {
    provider: string
    configured: boolean
    routes: Record<string, boolean>
  }
}

export type RunStatus =
  | 'pending'
  | 'running'
  | 'waiting_approval'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface AgentRun {
  id: string
  case_id: string
  turn_id: string | null
  parent_run_id: string | null
  agent: string
  status: RunStatus
  objective: string
  model_route: string
  input_tokens: number
  output_tokens: number
  tool_call_count: number
  estimated_cost: number
  error_code: string | null
  error: string | null
  metadata_json: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface RunEvent {
  id: number
  run_id: string
  event_type: string
  agent: string
  skill: string | null
  tool_call_id: string | null
  tool: string | null
  status: string
  payload: Record<string, unknown>
  created_at: string
}

export interface TurnRecord {
  id: string
  case_id: string
  role: string
  content: string
  created_at: string
}

export type ArtifactKind =
  | 'opinion_analysis'
  | 'propagation_reconstruction'
  | 'fact_check'
  | 'evidence_review'
  | 'report'
  | 'citation_validation'
  | (string & {})

export interface Artifact<T = Record<string, unknown>> {
  id: string
  case_id: string
  task_id: string | null
  run_id: string | null
  kind: ArtifactKind
  title: string
  version: number
  data: T
  created_at: string
}

// ---------- 专家 Artifact 数据形状（与 agents.py 指令一致） ----------

export interface OpinionConclusion {
  claim: string
  evidence_ids: string[]
  confidence: number
}

export interface OpinionData {
  conclusions: OpinionConclusion[]
  statistics: Record<string, unknown>
  explanation?: { text: string; evidence_ids: string[]; source?: string }
  limitations: string[]
}

export interface PropagationNode {
  id: string
  platform: string
}

export interface PropagationEdge {
  edge_id?: string
  source: string
  target: string
  relation: 'observed' | 'inferred'
  confidence: number
  reasons: string[]
}

/** Persisted edge with human-confirmation state (GET /cases/{id}/propagation-edges). */
export interface PropagationEdgeState {
  id: string
  source_post_id: string
  target_post_id: string
  relation: string
  confidence: number
  human_confirmed: boolean
}

export interface OriginCandidate {
  node_id: string
  confidence: number
  reason: string
}

export interface PropagationData {
  nodes: PropagationNode[]
  edges: PropagationEdge[]
  origin_candidates: OriginCandidate[]
  limitations: string[]
}

export type FactVerdict =
  | 'supported'
  | 'refuted'
  | 'insufficient'
  | 'misleading'

export interface FactCheckCard {
  id?: string
  claim: string
  verdict: FactVerdict
  confidence: number
  reason: string
  supporting_evidence: string[]
  contradicting_evidence: string[]
  temporal_consistency?: 'pass' | 'fail' | 'unknown'
  subject_consistency?: 'pass' | 'fail' | 'unknown'
  context_consistency?: 'pass' | 'fail' | 'unknown'
  checks?: string[]
}

export interface FactCheckData {
  cards: FactCheckCard[]
  limitations: string[]
}

export interface EvidenceReviewVerdict {
  target: string
  verdict: 'supported' | 'unsupported' | 'overreach'
  reason: string
  evidence_ids: string[]
}

export interface EvidenceReviewData {
  verdicts: EvidenceReviewVerdict[]
}

export interface ReportSection {
  id?: string
  title: string
  content: string
  evidence_ids?: string[]
}

export interface ReportData {
  schema_version?: string
  title: string
  executive_summary: string
  sections: ReportSection[]
  citation_links: Array<{ conclusion: string; evidence_ids: string[] }>
  disclaimer: string
}

export interface CitationCheck {
  citation: string
  verdict: 'valid' | 'invalid' | 'not_found'
  reason: string
}

export interface CitationValidationData {
  checks: CitationCheck[]
}

// ---------- Trace（GET /runs/{id}/trace） ----------

export interface ModelCallTrace {
  id: string
  run_id: string
  model: string
  route: string
  status: string
  input_tokens: number
  cached_input_tokens: number
  output_tokens: number
  estimated_cost: number
  currency: string
  pricing_model: string | null
  latency_ms: number
  error_code: string | null
  created_at: string
}

export interface ToolCallTrace {
  id: string
  run_id: string
  tool_name: string
  skill_name: string | null
  status: string
  arguments: Record<string, unknown>
  result: Record<string, unknown>
  error_code: string | null
  input_summary: string | null
  output_summary: string | null
  retry_count: number
  duration_ms: number
  estimated_cost: number
  idempotency_key: string | null
  approval_id: string | null
  rag: { available: boolean; hit_count: number; retrieval_modes: string[] } | null
  started_at: string
  finished_at: string | null
}

export interface ApprovalTrace {
  id: string
  run_id: string
  action: string
  reason: string
  status: string
  request_payload: Record<string, unknown>
  decision_payload: Record<string, unknown>
  decided_at: string | null
  created_at: string
}

export interface RunTrace {
  run: AgentRun
  model_calls: ModelCallTrace[]
  tool_calls: ToolCallTrace[]
  approvals: ApprovalTrace[]
  events: RunEvent[]
}

// ---------- 审批卡片（来自 approval_pending / approval_required 事件 payload） ----------

export interface ApprovalInfo {
  id: string
  action: string
  reason: string
  status: string
  request_payload: Record<string, unknown>
}

// ---------- Artifact 版本与报告导出 ----------

export interface ArtifactDiff {
  title_changed: boolean
  summary_changed: boolean
  sections_added: string[]
  sections_removed: string[]
  sections_changed: string[]
  citation_link_count: number
}

// ---------- 对话流条目 ----------

export interface RunArtifacts {
  artifact: Artifact
}

export type ChatItem =
  | { type: 'turn'; turn: TurnRecord }
  | {
      type: 'run'
      run: AgentRun
      artifacts: Artifact[]
      approvals: ApprovalInfo[]
      trace: RunTrace | null
      traceLoading: boolean
      artifactsError?: boolean
      // 内联 Harness 过程：SSE 实时增量按 run 分发到气泡内展示。
      // 终态后由 getRunTrace 的全量数据覆盖（与 trace 保持同步）。
      liveEvents: RunEvent[]
      liveToolCalls: ToolCallTrace[]
      liveModelCalls: ModelCallTrace[]
      // 模型最终回答（run 完成后紧邻的 assistant turn 合并进卡片顶部，
      // 与执行过程同属一个对话回合；历史数据/孤儿 turn 仍独立展示）。
      finalContent?: string
    }
  | { type: 'orphan-artifacts'; artifacts: Artifact[] }

// ---------- Evidence 侧栏（GET /cases/{id}/evidence-summary） ----------

export type EvidenceStance = 'support' | 'oppose' | 'context'

export interface EvidenceItem {
  id: string
  case_id: string
  claim_id: string | null
  source_type: string
  source_id: string
  stance: EvidenceStance | (string & {})
  excerpt: string
  relevance: number
  metadata_json: Record<string, unknown>
  created_at: string
}

export interface ClaimEvidence {
  id: string
  text: string
  status: string
  verdict: string | null
  confidence: number
  created_at: string
  evidence: EvidenceItem[]
}

export interface EvidenceSummary {
  case_id: string
  claims: ClaimEvidence[]
  unassigned: EvidenceItem[]
}

// ---------------- 跨平台对齐 ----------------

export interface PlatformParticipation {
  platform: string
  posts: number
  total_engagement: number
  avg_engagement: number
}

export interface PlatformSentiment {
  platform: string
  distribution: Record<string, number>
}

export interface PlatformTimelinePoint {
  platform: string
  window: string
  posts: number
}

export interface PlatformTerms {
  platform: string
  terms: string[]
}

export interface CommonTerm {
  term: string
  platforms: string[]
}

export interface PlatformComparison {
  platforms: string[]
  participation: PlatformParticipation[]
  sentiment: PlatformSentiment[]
  timeline: PlatformTimelinePoint[]
  topic_terms: PlatformTerms[]
  common_terms: CommonTerm[]
  insights: string[]
}

// ---------------- 持续监测与告警（01） ----------------

export interface MonitorDefinition {
  id: string
  case_id: string
  name: string
  enabled: boolean
  schedule_type: 'interval' | 'cron'
  interval_seconds: number | null
  cron: string | null
  timezone: string
  query_spec: Record<string, unknown>
  platforms: string[]
  account_watchlist: Record<string, unknown>[]
  lookback_seconds: number
  analysis_policy: Record<string, unknown>
  version: number
  created_at: string
  updated_at: string
}

export interface AlertRule {
  id: string
  monitor_id: string
  rule_type: string
  parameters: Record<string, unknown>
  severity: 'info' | 'warning' | 'critical'
  cooldown_seconds: number
  enabled: boolean
  version: number
  created_at: string
  updated_at: string
}

export interface MonitorExecution {
  id: string
  monitor_id: string
  scheduled_at: string
  started_at: string | null
  finished_at: string | null
  window_start: string | null
  window_end: string | null
  status: string
  run_id: string | null
  platform_stats: Record<string, unknown>
  error_code: string | null
  next_retry_at: string | null
  created_at: string
}

export type AlertStatus = 'open' | 'acknowledged' | 'resolved' | 'suppressed'

export interface MonitorAlert {
  id: string
  monitor_id: string
  rule_id: string
  fingerprint: string
  cooldown_bucket: string
  first_seen_at: string
  last_seen_at: string
  trigger_count: number
  status: AlertStatus
  evidence_refs: Record<string, unknown>
  metric_snapshot: Record<string, unknown>
  explanation: string
  acknowledged_by: string | null
  acknowledged_at: string | null
  created_at: string
  updated_at: string
}

// ---------------- 多模态媒体流水线（04） ----------------

export interface MediaAsset {
  id: string
  case_id: string
  post_id: string | null
  platform: string
  media_type: 'image' | 'video' | 'audio'
  url: string
  normalized_url: string
  source_kind: string
  storage_uri: string | null
  byte_size: number
  mime_type: string
  duration_ms: number | null
  width: number | null
  height: number | null
  download_status: string
  analysis_status: string
  error_code: string | null
  actual_sha256: string | null
  hash_kind: string
  phash: string | null
  ocr_text: string | null
  keyframe_urls: string[]
  c2pa_status: string | null
  pipeline_version: string
  metadata_json: Record<string, unknown>
  created_at: string
}

export interface MediaTranscript {
  id: string
  asset_id: string
  kind: string
  language: string
  segments: Record<string, unknown>[]
  full_text: string
  confidence: number
  provider: string
  version: string
  created_at: string
}

export interface MediaAssetDetail extends MediaAsset {
  transcripts: MediaTranscript[]
}

// ---------------- 跨平台对齐（06） ----------------

export interface AlignmentCandidate {
  id: string
  case_id: string
  left_type: string
  left_id: string
  right_type: string
  right_id: string
  relation_type: string
  feature_scores: Record<string, number>
  combined_score: number
  decision: 'pending' | 'confirmed' | 'probable' | 'possible' | 'rejected'
  review_id: string | null
  model_version: string
  created_at: string
  updated_at: string
}

// ---------------- 完整性风险（07） ----------------

export interface RiskAssessment {
  id: string
  case_id: string
  subject_type: string
  subject_id: string
  risk_type: 'automation' | 'marketing' | 'inauthenticity'
  score: number
  band: 'low' | 'medium' | 'high'
  reason_codes: string[]
  evidence_refs: Record<string, unknown>
  model_version: string
  status: 'signal_only' | 'reviewed_likely' | 'reviewed_unlikely' | 'inconclusive'
  reviewed_by: string | null
  review_note: string
  reviewed_at: string | null
  created_at: string
  updated_at: string
}

export interface CoordinationMember {
  id: string
  cluster_id: string
  account_id: string
  membership_score: number
  role: string
  evidence: Record<string, unknown>
  created_at: string
}

export interface IntegrityViews {
  raw: { post_count: number; engagement_total: number }
  downweighted: { post_count: number; engagement_total: number }
  excluded: { post_count: number; engagement_total: number }
  high_risk_accounts: number
  delta: { post_count: number; engagement_total: number }
}

export interface CoordinationCluster {
  id: string
  case_id: string
  window_start: string | null
  window_end: string | null
  algorithm_version: string
  size: number
  score: number
  explanation: string
  review_status: string
  created_at: string
}

// ---------------- 不确定性与偏差（08） ----------------

export interface QualityAssessment {
  id: string
  case_id: string
  target_type: string
  target_id: string
  dimension: string
  level: 'high' | 'medium' | 'low' | 'insufficient'
  score: number | null
  method: string
  inputs: Record<string, unknown>
  limitations: string[]
  version: string
  created_at: string
}

export interface AlternativeHypothesis {
  id: string
  case_id: string
  statement: string
  prediction: string
  supporting_evidence: string[]
  opposing_evidence: string[]
  status: string
  proposer: string
  review_notes: Record<string, unknown>
  created_at: string
  updated_at: string
}

// ---------------- 审核工作台（09） ----------------

export interface ReviewItem {
  id: string
  case_id: string
  object_type:
    | 'evidence'
    | 'claim'
    | 'propagation_edge'
    | 'alignment_candidate'
    | 'risk_assessment'
    | 'hypothesis'
    | 'report_conclusion'
  object_id: string
  priority: number
  status:
    | 'unreviewed'
    | 'in_review'
    | 'accepted'
    | 'rejected'
    | 'needs_more_evidence'
    | 'superseded'
  risk_level: 'low' | 'medium' | 'high'
  queue: string
  summary: string
  current_version: number
}

export interface ReviewQueueItem extends ReviewItem {
  decisions: { id: string; decision: string; reason: string; actor: string; created_at: string | null }[]
  comments: { id: string; text: string; actor: string; reference: string; created_at: string | null }[]
}

export interface ActivityEvent {
  id: string
  activity_type: string
  summary: string
  actor: string
  ref_run_id: string | null
  metadata: Record<string, unknown>
  created_at: string | null
}

// ---------------- 叙事生命周期（10） ----------------

export interface Narrative {
  id: string
  case_id: string
  title: string
  canonical_summary: string
  status: string
  review_state: string
  created_at: string
}

export interface NarrativeVersion {
  id: string
  narrative_id: string
  data_watermark: string | null
  algorithm_version: string
  keywords: string[]
  metrics: Record<string, unknown>
  created_at: string
}

export interface NarrativeDetail extends Narrative {
  versions: NarrativeVersion[]
  members: { claims: string[]; posts: string[] }
  timeline: { bucket: string; platform: string; volume: number; unique_accounts: number; engagement: number; stage: string }[]
}

export interface NarrativeTimeline {
  stages: string[]
  buckets: string[]
  series: number[]
  smoothed: number[]
  notes: string[]
  algorithm_version: string
}

export interface CorrectionEvent {
  id: string
  case_id: string
  source_post_id: string | null
  claim_id: string | null
  target_narrative_id: string | null
  correction_type: string
  content: string
  publisher_class: string
  review_state: string
  created_at: string
}

// ---------------- 语义分析（11） ----------------

export interface LexiconEntry {
  id: string
  term: string
  normalized: string
  meaning: string
  domain: string
  platform: string
  language: string
  valid_from: string | null
  valid_to: string | null
  source: string
  review_state: string
  version: string
  created_at: string
}

export interface SemanticAnalysis {
  original: string
  normalized: string
  language: { language: string; ratios: Record<string, number>; mixed: boolean }
  lexicon_hits: { term: string; meaning: string; priority: number }[]
  results: {
    task: string
    label: string
    confidence: number
    provider: string
    span: [number, number] | null
    entity_ref: string | null
    uncertain: boolean
  }[]
  fallback: boolean
  semantic_version: string
}

export interface SemanticAnnotation {
  id: string
  source_type: string
  source_id: string
  task: string
  label: string
  span: [number, number] | null
  confidence: number
  provider: string
  model_version: string
  created_at: string
}

// ---------------- 订阅与通知（13） ----------------

export interface Subscription {
  id: string
  case_id: string
  name: string
  event_filters: string[]
  severity: string
  channel: 'inbox' | 'webhook'
  endpoint_id: string | null
  schedule: string
  quiet_hours: Record<string, unknown>
  enabled: boolean
  version: number
}

export interface NotificationEndpoint {
  id: string
  name: string
  url: string
  verification_state: string
  enabled: boolean
}

export interface NotificationEvent {
  id: string
  event_id: string
  event_type: string
  severity: string
  classification: string
  data: Record<string, unknown>
  occurred_at: string | null
}

export interface DeliveryAttempt {
  id: string
  event_id: string
  subscription_id: string
  endpoint_id: string
  attempt: number
  status: string
  http_status: number | null
  http_summary: string
  next_retry_at: string | null
  error_code: string | null
}

export interface ExportJob {
  id: string
  scope: string
  scope_ref: string
  format: string
  redaction_policy: string
  status: string
  artifact_id: string | null
  created_at: string | null
}

// ---------------- 工具沙箱（15） ----------------

export interface ToolSandboxCapability {
  name: string
  execution_class: 'trusted_in_process' | 'restricted_process' | 'container'
  network: { mode: string; domains?: string[] }
  secrets: string[]
  resources: Record<string, unknown>
  risk_level: 'low' | 'medium' | 'high'
  side_effects: string
  requires_approval: boolean
  enabled: boolean
}

export interface ToolSandboxCapabilities {
  policy_mode: 'audit_only' | 'enforce'
  container_supported: boolean
  tools: ToolSandboxCapability[]
}

export interface SandboxHealth {
  policy_mode: string
  container_supported: boolean
  restricted_executor: string
  note: string
}

// ---------------- 技能浏览 ----------------

export interface SkillInfo {
  name: string
  version: string
  description: string
  tools: string[]
  permissions: string[]
  inputs: string[]
  outputs: string[]
  cost_tokens: number
  cancellation: string
  loadable: boolean
}

// ---------------- 辩论 ----------------

export interface Debate {
  id: string
  case_id: string
  title: string
  status: 'in_progress' | 'completed'
  round: number
  platform_roles: { platforms: string[] }
  created_at: string
  updated_at: string
}

export interface DebateMessage {
  id: string
  debate_id: string
  role: 'platform_role' | 'user' | 'moderator'
  platform: string | null
  round: number
  content: string
  created_at: string
}

export interface DebateVote {
  id: string
  debate_id: string
  platform: string
  choice: string
  reason: string
  created_at: string
}

export interface DebateDetail extends Debate {
  messages: DebateMessage[]
  votes: DebateVote[]
}

// ---------------- 异步分析任务（A-02） ----------------

export interface AnalysisJob {
  id: string
  case_id: string
  job_type: string
  status:
    | 'pending'
    | 'running'
    | 'retry_wait'
    | 'succeeded'
    | 'failed_terminal'
    | 'cancelled'
  attempt: number
  max_attempts: number
  cancel_requested: boolean
  progress_json: Record<string, unknown>
  result_json: Record<string, unknown>
  error_code: string | null
  created_at: string
  updated_at: string
}

// ===========================================================================
// M16 不可信内容与注入防御
// ===========================================================================

export interface ContentSecuritySignal {
  name: string
  severity: string
  evidence: string
  score: number
}

export interface ContentSecurityPolicy {
  mode: string
  policy_version: string
  trust_levels: string[]
  detectors: string[]
  hard_boundaries: string[]
}

export interface ContentSecurityAssessment {
  id: string
  object_type: string
  object_id: string
  run_id: string | null
  trust_level: string
  score: number
  risk_signals: ContentSecuritySignal[]
  detector: string
  disposition: string
  reason: string
  content_hash: string
  review_state: string
  created_at: string | null
}

export interface GuardrailDecision {
  id: string
  stage: string
  run_id: string | null
  tool_call_id: string | null
  tool: string | null
  decision: string
  reason: string
  policy_version: string
  signal_ids: string[]
  content_hash: string
  summary: string
  created_at: string | null
}

export interface ContentSecuritySummary {
  by_disposition: Record<string, number>
  by_trust_level: Record<string, number>
  by_object_type: Record<string, number>
}

// ===========================================================================
// M17 显式目标、计划图与完成条件
// ===========================================================================

export interface GoalSummary {
  id: string
  case_id: string
  title: string
  objective: string
  constraints: string[]
  priority: string
  status: string
  version: number
  source: string
  created_at: string | null
  updated_at: string | null
}

export interface CriterionInfo {
  id: string
  criterion_type: string
  description: string
  target: Record<string, unknown>
  status: string
  required: boolean
}

export interface PlanVersionInfo {
  id: string
  goal_id: string
  version: number
  status: string
  planner: string
  frozen_at: string | null
}

export interface CompletionAssessmentInfo {
  id: string
  plan_version_id: string
  verifier: string
  result: string
  gaps: string[]
  created_at: string | null
}

export interface GoalDetail {
  goal: GoalSummary
  criteria: CriterionInfo[]
  plan_versions: PlanVersionInfo[]
  assessments: CompletionAssessmentInfo[]
}

export interface StepInfo {
  id: string
  step_key: string
  task: string
  agent_capability: string
  status: string
  budget_max_cost: number
  run_id: string | null
  retry_count?: number
}

export interface PlanEdgeInfo {
  source_step_key: string
  target_step_key: string
  edge_type: string
}

export interface PlanDetail {
  plan_version: PlanVersionInfo
  steps: StepInfo[]
  edges: PlanEdgeInfo[]
  ready_steps: string[]
  topological_order: string[]
}

export interface CreatePlanRequest {
  steps: Array<{
    step_key: string
    task: string
    agent_capability: string
    depends_on?: string[]
    budget_max_cost?: number
    max_turns?: number
  }>
  edges: Array<{
    source_step_key: string
    target_step_key: string
    edge_type?: string
  }>
  planner?: string
}

// ===========================================================================
// M19 端到端可观测性与 SLO
// ===========================================================================

export interface SloResult {
  name: string
  description: string
  kind: string
  version: string
  total: number
  ok: number
  actual: number
  target: number
  budget_remaining: number
  burn_rate: number
  violated: boolean
}

export interface TelemetryHealth {
  status: string
  exporter: string
  span_count: number
  missing_attribute_count: number
  metrics_summary: Record<string, unknown>
  slo: SloResult[]
  policy_version: string
}

// ===========================================================================
// M20 真实数据评测、回归门禁与在线质量监控
// ===========================================================================

export interface EvaluationDataset {
  id: string
  name: string
  version: string
  task: string
  license: string
  content_hash: string
  example_count: number
  train_holdout: boolean
  created_at: string | null
}

export interface EvaluationRunSummary {
  id: string
  suite: string
  candidate_version: string
  baseline_version: string
  status: string
  aggregate: Record<string, number>
  created_at: string | null
  finished_at: string | null
}

export interface GateResultInfo {
  gate_id: string
  decision: string
  reason: string
  exempted_by: string | null
  exempt_expires_at: string | null
}

export interface EvaluationRunDetail extends EvaluationRunSummary {
  results: Record<string, unknown>
  error_samples: Array<{ evaluator: string; error: string }>
  gate_results: GateResultInfo[]
}

export interface ReleaseGateInfo {
  id: string
  name: string
  suite: string
  thresholds: Record<string, number>
  relative_regression_limits: Record<string, number>
  mandatory: boolean
  enabled: boolean
}

export interface DriftResult {
  psi: number
  js: number
  drifted: boolean
  signal_only: boolean
  message: string
}

// ===========================================================================
// M21 广义人工介入与反馈闭环
// ===========================================================================

export interface ApprovalInboxItem {
  id: string
  run_id: string
  action: string
  reason: string
  status: string
  approval_type: string
  risk_level: string
  scope: string
  requested_action: string
  redacted_preview: string
  allowed_decisions: string[]
  expires_at: string | null
  decision_payload: Record<string, unknown>
  decided_at: string | null
  actor: string | null
  created_at: string | null
  request_summary: string
  approval_kind: string | null
}

export interface ApprovalDecisionResponse {
  id: string
  run_id: string
  action: string
  status: string
  decided_at: string | null
}

export interface ApprovalStats {
  total: number
  decided: number
  approved: number
  approved_with_edits: number
  rejected: number
  expired: number
  cancelled: number
  approval_rate: number
  edit_rate: number
  rejection_rate: number
  expiry_rate: number
}

// ---------- M22 故障隔离、降级与事故处置 ----------

export interface DependencyHealth {
  dependency: string
  scope: string
  status: 'healthy' | 'degraded' | 'outage' | 'auth_required' | 'policy_denied'
  error_code: string
  circuit_state: 'closed' | 'open' | 'half_open'
  consecutive_failures: number
  last_success_at: string | null
  last_failure_at: string | null
}

export interface ResilienceHealthSummary {
  healthy: number
  degraded: number
  outage: number
  auth_required: number
  policy_denied: number
  dependencies: DependencyHealth[]
}

export interface CircuitBreakerState {
  dependency: string
  scope: string
  state: 'closed' | 'open' | 'half_open'
  failure_count: number
  success_count: number
  config_version: string
  opened_at: string | null
  half_open_probe_at: string | null
  updated_at: string | null
}

export interface DeadLetterItem {
  id: string
  operation_key: string
  dependency: string
  scope: string
  error_classification: string
  error_code: string
  attempts: number
  payload_hash: string
  policy_version: string
  code_version: string
  recovery_hint: string
  payload_ref: string
  status: 'pending' | 'approved' | 'retrying' | 'resolved' | 'discarded'
  created_at: string | null
  resolved_at: string | null
}

export interface KillSwitch {
  id: string
  scope: string
  target: string
  status: 'on' | 'off'
  reason: string
  actor: string
  approval_id: string | null
  created_at: string | null
  disabled_at: string | null
}

export interface IncidentRecord {
  id: string
  title: string
  severity: 'info' | 'warning' | 'critical'
  status: 'open' | 'closed'
  impact: string
  timeline: Array<Record<string, unknown>>
  metrics: Record<string, unknown>
  actions: Array<Record<string, unknown>>
  recovery: Record<string, unknown>
  retro: Record<string, unknown>
  kill_switch_ids: string[]
  created_at: string | null
  closed_at: string | null
}

// ---------- M23 记忆安全与用户可控治理 ----------

export type MemoryStatus =
  | 'active'
  | 'pending_review'
  | 'superseded'
  | 'expired'
  | 'disabled'
  | 'deleted'

export interface MemoryRecord {
  id: string
  case_id: string | null
  scope: string
  kind: string
  content: string
  source_type: string
  source_id: string
  importance: number
  confidence: number
  active: boolean
  supersedes_id: string | null
  metadata_json: Record<string, unknown>
  created_at: string
  updated_at: string
  memory_type: string | null
  trust_level: string | null
  review_state: string | null
  confidence_level: string | null
  valid_from: string | null
  expires_at: string | null
  last_verified_at: string | null
  content_hash: string | null
  version: number | null
  sensitivity: string | null
  index_status: string | null
  embedding_version: string | null
  write_policy_version: string | null
  status: MemoryStatus | null
}

export interface MemoryMutationEntry {
  id: string
  action: string
  actor: string
  reason: string
  from_status: string
  to_status: string
  version_before: number
  version_after: number
  created_at: string | null
}

export interface MemoryAccessEvent {
  id: string
  run_id: string | null
  purpose: string
  result_count: number
  created_at: string | null
}

export interface MemoryConflict {
  id: string
  conflicting_memory_id: string
  content_hash: string
  resolved: boolean
  resolution: string
  resolved_by: string | null
  created_at: string | null
  resolved_at: string | null
}
