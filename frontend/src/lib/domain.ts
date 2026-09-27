export type Metrics = {
  reviews: number
  observations: number
  issues: number
  none_reviews: number
  issue_reviews: number
  coverage: number
}

export type Category = { key: string; label: string; observations: number; issues: number }
export type TranslationStatus = 'ready' | 'source' | 'pending' | 'failed'

export type Evidence = {
  observation_id?: string
  quote: string
  quote_zh: string | null
  translation_status: TranslationStatus
  review_id: string
  language: string
  recommended: boolean | null
  created_at: string | null
  updated_at: string | null
  url: string
}

export type Issue = {
  id: string
  title: string
  category: string
  category_label: string
  description: string
  reviews: number
  review_ids: string[]
  claim_counts: Record<string, number>
  claim_review_counts?: { complaint: number; worsened: number; improved: number; unverified: number }
  observations: number
  evidence: string[]
  evidence_items: Evidence[]
  impact: string
  evidence_capsule?: {
    version: string
    review_count: number
    observation_count: number
    manifestations: { id: string; label: string; review_count: number; observation_count: number; observation_ids: string[]; representatives: { observation_id: string; description: string }[] }[]
  }
  player_themes?: { title: string; object: string; symptom: string; claim: string; reviews: number; observations: number; evidence_items: Evidence[] }[]
}

export type AnalysisContext = { key: string; kind: 'issue' | 'category' | 'comparison'; label: string; issueIds: string[]; summary: string; filtered?: boolean; tracking?: { scope: import('./tracking').ComparisonScope; issueId?: string; savedId?: string } }

export type Overview = {
  game: { id: string; name: string; app_id: number; steam_url: string }
  source: string
  created_at: string
  metrics: Metrics
  caveat: string
  top_issues: Issue[]
  featured_evidence: { issue: Issue; evidence: Evidence }[]
  categories: Category[]
  global: {
    rule_version: string
    scope: { collected: number; analyzed: number; with_feedback: number; without_feedback: number; pending: number; failed: number; excluded: number; coverage: string; observed_at: string | null }
    recommendation: { recommended: number; not_recommended: number; unknown: number; rate: number | null }
    timeline: { months: { period: string; reviews: number }[]; years: { period: string; reviews: number }[]; dated: number; undated: number; start: string | null; end: string | null }
    categories: (Category & { reviews: number; review_ids: string[]; share: number | null })[]
    issues: Issue[]
    top_categories: { keys: string[]; reviews: number; share: number | null }
    overlap: { single: number; multiple: number; share: number | null; pairs: { a: string; b: string; reviews: number; review_ids: string[]; share_a: number | null; share_b: number | null }[] }
    statements: string[]
    versions: Record<string, string>
  }
}

export type Settings = { provider: string; model: string; configured: boolean; status: 'missing' | 'configured' | 'error'; last_error: string }

export type ChatResponse = {
  answer: string
  caveat: string
  model: string
  seconds?: number
  tools: { name: 'show_stats' | 'show_chart' | 'show_table' | 'show_citation'; result: unknown }[]
}

export type DecisionBrief = {
  id: string
  type: 'act' | 'investigate' | 'validate' | 'monitor'
  type_label: string
  title: string
  action: string
  rationale: string
  basis_dimensions: ('coverage' | 'concentration' | 'specificity' | 'minority' | 'trend')[]
  judgment: string
  unknown: string
  issue: Issue
  evidence: Evidence[]
  facts: { label: string; value: string; detail: string }[]
  gate: {
    version: string
    coverage: { unique_reviews: number; observations: number; level: 'supported' | 'moderate' | 'limited' }
    concentration: { largest_manifestation_share: number; manifestation_count: number }
    specificity: { specific_observation_share: number }
    minority: { manifestation_count: number; present: boolean }
    trend: { available: boolean; reason: string }
    act_eligible: boolean
    allowed_actions: ('act' | 'investigate' | 'validate' | 'monitor')[]
    constraints: string[]
  }
  confidence: { level: 'supported' | 'moderate' | 'limited'; label: string; reasons: string[] }
  validation: { focus: string; metric: string; baseline: string; success_signal: string; minimum_sample: string; hypothesis: string; followup_window: string; observed_result: string; outcome: DecisionOutcome | null }
  review: { status: 'pending' | 'accepted' | 'modified' | 'rejected'; updated_at: string | null; reason: string; final_action: string }
  execution: { status: 'planned' | 'shipped' | 'cancelled'; shipped_at: string | null }
  outcome_tracking: { version: string; updated_at: string | null }
}

export type DecisionOutcome = 'improved' | 'unchanged' | 'worsened' | 'inconclusive'
export type DecisionOutcomeUpdate = {
  final_action: string
  execution: DecisionBrief['execution']
  validation: Pick<DecisionBrief['validation'], 'hypothesis' | 'metric' | 'baseline' | 'followup_window' | 'observed_result' | 'outcome'>
}

export type DecisionBriefResponse = {
  version: string
  status: 'not_generated' | 'ready'
  generated_at: string | null
  model: string | null
  decisions: DecisionBrief[]
  methodology: { selection_basis: string; trend: string; effort: string; guardrail: string }
}
