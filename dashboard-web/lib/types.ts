/** Response shapes, mirroring the API schemas. */

export type Verdict = "supported" | "contradicted" | "disputed" | "unsupported";

export interface User {
  id: string;
  email: string;
  display_name: string | null;
  is_admin: boolean;
  auth_provider: string;
  created_at?: string | null;
}

export interface Citation {
  n: number;
  chunk_id: number;
  document_id: string;
  version_id: number;
  title: string;
  url: string | null;
  source: string;
  date: string | null;
  quote: string;
  char_start: number;
  char_end: number;
  page: number | null;
  score?: number;
}

export interface SearchHit {
  chunk_id: number;
  document_id: string;
  version_id: number;
  title: string;
  url: string | null;
  source_id: string;
  source_name: string;
  source_type: string;
  published_at: string | null;
  text: string;
  char_start: number;
  char_end: number;
  page: number | null;
  snippet: string;
  scores: { lexical_rank: number | null; vector_rank: number | null; rrf: number; rerank: number | null };
}

export interface SearchResponse {
  hits: SearchHit[];
  mode_effective: string;
  timings_ms: Record<string, number>;
  total_candidates: number;
}

export interface SentenceVerdict {
  index: number;
  text: string;
  char_start: number;
  char_end: number;
  verdict: Verdict;
  confidence: number;
  method: string;
  rationale: string;
  cited_ok: boolean | null;
  flags: string[];
  supporting: Citation[];
  contradicting: Citation[];
}

export interface VerifyResponse {
  sentences: SentenceVerdict[];
  summary: Record<string, number>;
  timings_ms: Record<string, number>;
  method: string;
}

export interface AnswerSentence {
  index: number;
  text: string;
  char_start: number;
  char_end: number;
  verdict: Verdict;
  confidence: number;
  citations: number[];
  flags: string[];
  method: string;
}

export interface AnswerRecord {
  id: string;
  question: string;
  answer_md: string;
  sentences: AnswerSentence[];
  citations: Citation[];
  method: string;
  created_at?: string | null;
}

export interface Source {
  id: string;
  source_type: string;
  name: string;
  url: string | null;
  canonical_url: string | null;
  status: string;
  watch: boolean;
  refresh_interval_minutes: number | null;
  next_refresh_at: string | null;
  last_ingested_at: string | null;
  created_at?: string | null;
  document_count: number;
}

export interface Document {
  id: string;
  source_id: string;
  identity_key: string;
  title: string;
  url: string | null;
  canonical_url: string | null;
  author: string | null;
  published_at: string | null;
  lang: string | null;
  current_version_id: number | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  text?: string;
  chunk_count?: number;
  version_count?: number;
}

export interface Version {
  id: number;
  document_id: string;
  version_no: number;
  content_hash: string;
  clean_text: string;
  page_map: { page: number; start: number; end: number }[] | null;
  origin: string;
  effective_at: string;
  fetched_at: string | null;
  archived_at: string | null;
  diff_stats: { sentences_added: number; sentences_removed: number } | null;
}

export interface Claim {
  id: number;
  document_id: string;
  version_id: number;
  chunk_id: number | null;
  statement: string;
  quote: string;
  char_start: number;
  char_end: number;
  subject: string;
  claim_type: string;
  numbers: { value: number; unit: string; text: string }[];
  polarity: number;
  asserted_at: string;
  valid_to: string | null;
  status: string;
  superseded_by: number | null;
  cluster_id: number | null;
  confidence: number;
  extractor: string;
}

export interface ClaimRelation {
  id: number;
  a_claim_id: number;
  b_claim_id: number;
  relation: string;
  direction: string | null;
  confidence: number;
  method: string;
  rationale: string;
  signals: Record<string, unknown>;
  user_label: string | null;
  resolved_at: string | null;
}

export interface ClaimDetail extends Claim {
  relations: ClaimRelation[];
  events: { id: number; claim_id: number; event: string; at: string; observed_at: string }[];
}

export interface Cluster {
  id: number;
  label: string;
  label_source: string;
  size: number;
  doc_count: number;
  conflict_count: number;
  topic_id: number | null;
  camps?: {
    balanced: boolean;
    camps: {
      label: string;
      claim_ids: number[];
      claims: {
        claim_id: number;
        statement: string;
        quote: string;
        status: string;
        date: string | null;
        document_id: string | null;
        title: string;
        url: string | null;
        source: string;
        char_start: number;
        char_end: number;
      }[];
    }[];
  };
  claim_ids?: number[];
}

export interface Conflict {
  id: number;
  a: Claim;
  b: Claim;
  confidence: number;
  rationale: string;
  cluster_id: number | null;
  resolved_at: string | null;
}

export interface TimelineEntry {
  at: string;
  observed_at: string;
  event: string;
  label: string;
  claim_id: number;
  statement: string;
  document_id: string | null;
  document_title: string;
  cluster_id: number | null;
  related_claim_id: number | null;
  related_statement: string | null;
}

export interface StaleNote {
  claim_id: number;
  statement: string;
  document_id: string;
  document_title: string;
  reasons: string[];
  asserted_at: string | null;
  evidence: { claim_id: number; statement: string; status: string; document_id: string; asserted_at: string }[];
}

export interface BeliefDiff {
  id: string;
  scope_kind: string;
  scope_id: string | null;
  period_start: string;
  period_end: string;
  payload: Record<string, unknown>;
  digest_md: string;
  digest_method: string;
  stats: Record<string, number>;
  created_at?: string | null;
}

export interface WikiPage {
  id: number;
  topic_id: number;
  slug: string;
  title: string;
  status: string;
  revision: number;
  compiled_at: string | null;
  compile_method: string;
  error: string | null;
  sentence_count: number;
  lint_count: number;
  body?: WikiBody;
  body_md?: string;
  claim_ids?: number[];
  backlinks?: string[];
}

export interface WikiSentence {
  text: string;
  claim_ids: number[];
}

export interface WikiSection {
  id: string;
  heading: string;
  kind: "overview" | "consensus" | "disputed" | "history" | "details";
  sentences: WikiSentence[];
}

export interface WikiBody {
  title: string;
  summary: WikiSentence[];
  sections: WikiSection[];
  see_also: string[];
}

export interface Topic {
  id: number;
  slug: string;
  label: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface LintFinding {
  id?: number | null;
  page_id: number | null;
  kind: string;
  severity: string;
  detail: Record<string, unknown>;
  resolved_at: string | null;
}

export interface Job {
  id: number;
  user_id: string | null;
  kind: string;
  payload: Record<string, unknown>;
  status: string;
  priority: number;
  run_after: string;
  attempts: number;
  max_attempts: number;
  locked_by: string | null;
  last_error: string | null;
  result: Record<string, unknown> | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface Watch {
  id: string;
  kind: string;
  source_id: string | null;
  query: string | null;
  created_at: string | null;
  last_checked_at: string | null;
}

export interface ApiToken {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  token?: string | null;
}

export interface LLMSettings {
  provider: string;
  base_url: string;
  model_fast: string;
  model_smart: string;
  auth: string;
  auth_header: string;
  json_mode: string;
  daily_token_budget: number;
  preset: string;
  presets: string[];
  has_api_key?: boolean;
  token_command_set?: boolean;
  has_oauth?: boolean;
  oauth_expires_at?: string | null;
}

export interface UsageReport {
  days: number;
  totals: {
    calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    cache_hits: number;
    errors: number;
    cache_hit_rate: number;
    error_rate: number;
  };
  by_day: Record<string, { calls: number; prompt_tokens: number; completion_tokens: number }>;
  by_task: Record<string, { calls: number; prompt_tokens: number; completion_tokens: number }>;
}

export interface IngestionRun {
  id: number;
  source_id: string | null;
  job_id: number | null;
  status: string;
  documents_found: number;
  documents_new: number;
  documents_changed: number;
  documents_unchanged: number;
  chunks_inserted: number;
  error: string | null;
  warnings: unknown[];
  started_at: string | null;
  finished_at: string | null;
}
