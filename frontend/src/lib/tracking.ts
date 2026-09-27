import type { TranslationStatus } from './domain'

export type Snapshot = {
  id: string
  demo?: boolean
  scope?: Record<string, string> | null
  scope_declaration?: { text: string; source: string; recorded_at: string } | null
  outside_definition?: number
  sample_kind?: string
  review_start?: string | null
  review_end?: string | null
  label: string
  observed_at: string
  source: string
  coverage: string
  total: number
  analyzed: number
  excluded?: number
  failed: number
  observations: number
  issues: number
  recommended: number
  negative: number
  unknown: number
  complaints: number
  complaint_share: number | null
  pending: number
  unassigned: number
  started_at: string | null
  positive_share: number | null
  status: 'complete' | 'partial'
  analysis_version: string
}

export type TrackingJob = { status: 'idle' | 'running' | 'error' | 'complete'; message: string; started_at?: string; finished_at?: string }
export type Metric = 'mentions' | 'complaints' | 'improved'
export type ComparisonScope = Record<string, string> & { before: string; after: string; mode: 'snapshots' | 'periods'; metric: Metric; language: string; offset: string }
export type TrackingEvent = { id: string; name: string; at: string; precision: 'time' | 'day'; url: string }
export type TrackingCatalog = { status: 'waiting' | 'ready' | 'unavailable'; selection: { before: string; after: string } | null; selection_note: string; reasons: string[]; snapshots: Snapshot[]; job: TrackingJob; model: string; analysis_version: string; translation_revision: number; languages: string[] }
export type Observation = { observation_id: string; issue_id?: string | null; category: string; object: string; symptom: string; description: string; evidence: string; evidence_zh: string | null; claim: string; change_evidence: string; change_evidence_zh: string | null; translation_status: TranslationStatus; change_translation_status: TranslationStatus }
export type TrackingIssue = {
  id: string
  definition_ref: string
  title: string
  category: string
  before_count: number
  after_count: number
  before_share: number
  after_share: number
  delta_count: number
  delta_pp: number
  note: string
  before_observation_count: number
  after_observation_count: number
}
export type TrackingComparison = {
  scope: ComparisonScope
  status: 'ready' | 'unavailable'
  before: Snapshot
  after: Snapshot
  comparable: boolean
  reasons: string[]
  issues: TrackingIssue[]
  summary: string
  collection_notes?: string[]
  analysis?: { label: string; analyzed_at: string; definition_ref: string; definition_source: { label: string }; note: string } | null
}
export type TrackingEvidence = { snapshot_id: string; observed_at: string; total: number; offset: number; source_url: string; items: { review_id: string; text: string; text_zh: string | null; translation_status: TranslationStatus; recommended: boolean; created_at: string; language: string; observations: Observation[] }[] }
export type TrackingMonitoring = {
  latest?: TrackingComparison | null
  points: { snapshot: Snapshot; values: Record<string, { count: number; share: number; observations: number }>; reasons: string[] }[]
  issues: { id: string; title: string }[]
  definition_ref: string | null
  analysis: TrackingComparison['analysis']
}
export type TrackingChartData = { points: { at: string; end?: string; side?: string; snapshot_id?: string; total: number; analyzed: number; count: number | null; share: number | null; value: number | null; complete: boolean; small: boolean }[]; events: TrackingEvent[]; reasons: string[]; granularity: 'snapshot' | 'day' | 'week'; measure: 'share' | 'count'; issue_id: string | null; issues: { id: string; title: string }[] }
export type ReviewHistory = { review_id: string; history: { snapshot: Snapshot; text: string | null; text_zh: string | null; translation_status: TranslationStatus; recommended: boolean | null; status: 'analyzed' | 'pending' | 'not_seen'; observations: Observation[]; created_at?: string | null; updated_at?: string | null; review_version_id?: string | null }[] }

export const metricLabels: Record<Metric, string> = { mentions: '全部提及', complaints: '投诉表述', improved: '明确改善' }
export const opinion = (value: boolean | null) => value === true ? '推荐' : value === false ? '不推荐' : '推荐状态未知'
