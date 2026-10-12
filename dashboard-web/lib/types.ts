/**
 * Response shapes for the dashboard.
 *
 * Most types are aliases of the generated OpenAPI schemas in ./api-types.ts.
 * Types kept hand-written are narrower UI shapes or have no generated
 * counterpart; each carries a comment saying why.
 */
import type { components } from "./api-types";

type Schemas = components["schemas"];

/** Kept hand-written: a string union the UI relies on; the generated schema types verdict as plain string. */
export type Verdict = "supported" | "contradicted" | "disputed" | "unsupported";

/** Replace a generated `verdict: string` with the narrower UI union. */
type WithVerdict<T> = Omit<T, "verdict"> & { verdict: Verdict };

export type User = Schemas["UserOut"];

export type Citation = Omit<Schemas["Citation"], "score"> & { score?: number };

/** Hit with the score breakdown spelled out (generated `scores` is optional and loosely typed). */
export type SearchHit = Omit<Schemas["HitOut"], "scores"> & {
  scores: { lexical_rank: number | null; vector_rank: number | null; rrf: number; rerank: number | null };
};

export type SearchResponse = Omit<Schemas["SearchResponse"], "hits"> & { hits: SearchHit[] };

export type SentenceVerdict = WithVerdict<Schemas["SentenceVerdictOut"]>;

export type VerifyResponse = Omit<Schemas["VerifyResponse"], "sentences"> & { sentences: SentenceVerdict[] };

export type AnswerSentence = WithVerdict<Schemas["AnswerSentence"]>;

export type AnswerRecord = Omit<Schemas["AnswerOut"], "sentences"> & { sentences: AnswerSentence[] };

export type Source = Schemas["SourceOut"];

/** Document list item, with the detail-only fields optional. */
export type Document = Schemas["DocumentOut"] &
  Partial<Pick<Schemas["DocumentDetailOut"], "text" | "chunk_count" | "version_count" | "starred" | "archived">>;

/** Kept hand-written: `page_map` and `diff_stats` are typed here; the generated schema uses `unknown[]`. */
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

/** Claim with `numbers` typed; the generated schema uses `unknown[]`. */
export type Claim = Omit<Schemas["ClaimOut"], "numbers"> & {
  numbers: { value: number; unit: string; text: string }[];
};

export type ClaimRelation = Schemas["RelationOut"];

export type ClaimDetail = Claim & Pick<Schemas["ClaimDetailOut"], "relations" | "events">;

/** Camps are narrowed here: the generated `camps` is loosely typed and the cluster page reads its claims. */
export type Cluster = Omit<Schemas["ClusterOut"], "camps"> & {
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
};

export type Conflict = Omit<Schemas["ConflictOut"], "a" | "b"> & { a: Claim; b: Claim };

export type TimelineEntry = Schemas["TimelineEntryOut"];

export type StaleNote = Schemas["StaleNoteOut"];

/** Payload and stats are narrowed to the record types the digest UI reads. */
export type BeliefDiff = Omit<Schemas["DiffOut"], "payload" | "stats"> & {
  payload: Record<string, unknown>;
  stats: Record<string, number>;
};

export type WikiFootnote = Schemas["FootnoteOut"];

/** Kept hand-written: `kind` is a string union the UI switches on; the generated body is inline in PageDetailOut. */
export interface WikiSentence {
  text: string;
  claim_ids: number[];
}

/** Kept hand-written: see WikiSentence. */
export interface WikiSection {
  id: string;
  heading: string;
  kind: "overview" | "consensus" | "disputed" | "history" | "details";
  sentences: WikiSentence[];
}

/** Kept hand-written: see WikiSentence. */
export interface WikiBody {
  title: string;
  summary: WikiSentence[];
  sections: WikiSection[];
  see_also: string[];
}

/** List item plus the optional detail fields the page view loads. */
export type WikiPage = Schemas["PageOut"] &
  Partial<Omit<Schemas["PageDetailOut"], keyof Schemas["PageOut"] | "body" | "footnotes">> & {
    body?: WikiBody;
    footnotes?: WikiFootnote[];
  };

export type Topic = Schemas["TopicOut"];

export type LintFinding = Schemas["LintFindingOut"];

export type Job = Schemas["JobOut"];

export type Watch = Schemas["WatchOut"];

export type ApiToken = Schemas["TokenOut"];

/** Kept hand-written: no generated schema exists for it. */
export interface LLMSettings {
  provider: string;
  base_url: string;
  model_fast: string;
  model_smart: string;
  auth: string;
  auth_header: string;
  json_mode: string;
  daily_token_budget: number;
  /** TTL for the token command's output: the stored per-credential value, else the server default. */
  token_ttl_seconds: number;
  preset: string;
  presets: string[];
  has_api_key?: boolean;
  token_command_set?: boolean;
  has_oauth?: boolean;
  oauth_expires_at?: string | null;
}

/** Kept hand-written: no generated schema exists for it. */
export interface UsageReport {
  days: number;
  /** Today (UTC) against the effective budgets; the usage endpoint is admin-only. */
  today: { requests: number; request_budget: number; tokens: number; token_budget: number };
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

export type IngestionRun = Schemas["IngestionRunOut"];

export type EvalRun = Schemas["EvalRunOut"];
