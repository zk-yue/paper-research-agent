export interface StepInfo {
  label: string;
  status: 'pending' | 'running' | 'done' | 'error' | 'skipped';
  detail: string;
}

export interface PaperInfo {
  title: string;
  authors: string;
  url?: string;
  pdf_url?: string;
  paper_id?: string;
  summary_cn?: string;
  relevance_score?: number;
  year?: string;
  source?: string;
  citations?: number;
  relevance_reason?: string;
  discovery_round?: number;
  relevance_match_scope?: string;
  relevance_evidence?: string;
  matched_queries?: string[];
  citation_from?: Array<{ title?: string; paper_id?: string | null; openalex_id?: string | null }>;
}

export type SearchMode = 'keyword' | 'deep';

export interface TaskInfo {
  id: string;
  year_from: number;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  steps: StepInfo[];
  created_at: string;
  completed_at: string | null;
  results: Record<string, unknown> | null;
  error: string | null;
  quick_mode: boolean;
  keywords: string[];
  papers?: PaperInfo[];
  enable_literature?: boolean;
  topic?: string | null;
  search_mode?: SearchMode;
  deep_rounds?: number;
}

export interface CreateTask {
  year_from: number;
  enable_literature: boolean;
  quick_mode: boolean;
  keywords: string[];
  topic?: string | null;
  search_mode?: SearchMode;
  deep_rounds?: number;
}

export type WsMessage =
  | { type: 'init'; task: TaskInfo }
  | { type: 'task_update'; task: TaskInfo }
  | { type: 'not_found' }
  | { type: 'cancelled' }
  | { type: 'ping' }
  | { type: 'resync_required' }
  | { type: 'step_update'; step_idx: number; status: string; detail: string }
  | { type: 'stream_content'; step_idx: number; content: string }
  | { type: 'download_progress'; paper_idx: number; total: number; status: string; filename?: string }
  | { type: 'review_started'; review_id: string; paper_count: number }
  | { type: 'review_stream'; review_id: string; content: string }
  | { type: 'review_complete'; review_id: string; content: string }
  | { type: 'review_error'; review_id: string; error: string }
  | { type: 'card_extract_progress'; current: number; total: number; status: string }
  | { type: 'card_extract_complete'; card_count: number }
  | { type: 'card_stream'; paper_index: number; paper_title: string; content: string; status: 'extracting' | 'done' | 'error' }
  | { type: 'review_stage'; review_id: string; stage: string; message: string }
  | { type: 'prompt_log_entry'; entry: PromptLogEntry };

export interface CustomReview {
  id: string;
  created_at: string;
  paper_indices: number[];
  paper_titles: string[];
  instructions: string;
  content: string;
  status: string;
  mode?: 'survey' | 'related_work';
  language?: 'zh' | 'en';
  inferred_title?: string;
  stage?: string;
  message?: string;
  progress?: { completed: number; total: number };
  stages?: Array<{ key: string; label: string; status: string; detail?: string }>;
  sections?: Array<{ id: string; title: string; status: string; content?: string; error?: string }>;
  evidence_stats?: { selected: number; fulltext: number; abstract: number; metadata: number; html?: number; pdf?: number };
  coverage?: { total: number; cited: number; uncited_ids?: string[]; assigned?: number };
  quality?: { issues: string[]; invalid_citations: number };
  error?: string;
  updated_at?: string;
  resumable?: boolean;
}

export interface ReviewTemplate {
  id: string;
  journal_name: string;
  papers?: Array<{ title: string; content: string }>;
  content?: string;
  created_at: string;
  content_preview?: string;
  paper_count?: number;
}

export interface RefCardEvidence {
  claim: string;
  source_section: string;
  support: string;
}

export interface ReferenceCard {
  paper_id: string;
  title: string;
  authors: string;
  year: string;
  venue: string;
  source_url: string;
  problem: string;
  method: string;
  key_idea: string;
  main_contribution: string;
  strength: string;
  limitation: string;
  relation_to_current_work: string;
  suggested_related_work_role: string;
  evidence: RefCardEvidence[];
  confidence: 'high' | 'medium' | 'low';
  missing_information: string[];
}

/** UI-friendly summary of a Reference Card (verbose fields omitted). */
export interface ReferenceCardSummary {
  paper_id: string;
  paper_index: number;
  title: string;
  authors: string;
  year: string;
  venue: string;
  problem: string;
  method: string;
  key_idea: string;
  main_contribution: string;
  strength: string;
  limitation: string;
  relation_to_current_work: string;
  suggested_related_work_role: string;
  confidence: 'high' | 'medium' | 'low';
  missing_information: string[];
}

export interface InstructionPreset {
  id: string;
  label: string;
  content: string;
  created_at: string;
  content_preview?: string;
}

export interface PromptLogEntry {
  id: string;
  agent: string;
  system_prompt: string;
  user_prompt: string;
  response_preview: string;
  duration_ms: number;
  status: string;
  timestamp: number;
}

export interface TaskSummary extends Omit<TaskInfo, "papers" | "results"> {
  paper_count: number;
  review_count: number;
}

export interface RuntimeInfo { provider: string; model: string; incremental_output: boolean }
export interface DownloadResult { success: boolean; path?: string; error?: string }
