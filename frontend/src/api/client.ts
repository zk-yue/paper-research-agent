import type { CreateTask, TaskInfo, TaskSummary, RuntimeInfo, DownloadResult, ReviewTemplate, ReferenceCardSummary, InstructionPreset, PromptLogEntry } from './types';
import { request } from './request';

const segment = encodeURIComponent;
const taskPath = (id: string) => `/tasks/${segment(id)}`;
const post = (body?: unknown) => ({ method: 'POST', body });
const put = (body: unknown) => ({ method: 'PUT', body });
const remove = { method: 'DELETE' };

export const fetchRuntime = (signal?: AbortSignal) => request<RuntimeInfo>('/runtime', { signal });
export const fetchTaskSummaries = (signal?: AbortSignal) => request<TaskSummary[]>('/tasks?summary=true', { signal });
export const deleteTask = (id: string) => request<{ deleted: boolean }>(taskPath(id), remove);
export const fetchTasks = () => request<TaskInfo[]>('/tasks');
export const fetchTask = (id: string) => request<TaskInfo>(taskPath(id));
export const createTask = (data: CreateTask) => request<{ task_id: string }>('/tasks', post(data));
export async function cancelTask(id: string): Promise<void> {
  await request(`${taskPath(id)}/cancel`, post());
}
export const retryTask = (id: string) => request<{ task_id: string }>(`${taskPath(id)}/retry`, post());
export const expandKeywords = (keywords: string[]) => request<{ keywords: string[] }>('/expand-keywords', post({ keywords }));

export { subscribeWs } from './websocket';

export const listDownloadedPapers = (id: string) => request<{ paper_ids: string[] }>(`${taskPath(id)}/downloaded-papers`);
export async function downloadPapers(id: string, paperIndices: number[]): Promise<{ results: Record<string, DownloadResult> }> {
  return request(`${taskPath(id)}/download-papers`, post({ paper_indices: paperIndices }));
}
export async function downloadPapersFromHistory(taskIds: string[]): Promise<{ results: Record<string, Record<string, DownloadResult> | { error: string }> }> {
  return request('/download-papers-from-history', post({ task_ids: taskIds }));
}
export const writeReview = (id: string, paperIndices: number[], instructions = '', templateId = '', mode: 'survey' | 'related_work' = 'survey', language: 'zh' | 'en' = 'zh') =>
  request<{ review_id: string }>(`${taskPath(id)}/write-review`, post({ paper_indices: paperIndices, instructions, template_id: templateId, mode, language }));
export const resumeReview = (id: string, reviewId: string) =>
  request<{ review_id: string }>(`${taskPath(id)}/reviews/${segment(reviewId)}/resume`, post());
export const cancelReview = (id: string, reviewId: string) =>
  request<{ review_id: string }>(`${taskPath(id)}/reviews/${segment(reviewId)}/cancel`, post());
export const reviewExportUrl = (id: string, reviewId: string) =>
  `/api${taskPath(id)}/reviews/${segment(reviewId)}/export`;
export const fetchReferenceCards = (id: string) => request<{ cards: Record<string, ReferenceCardSummary> }>(`${taskPath(id)}/reference-cards`);
export const extractReferenceCards = (id: string, paperIndices: number[], forceRefresh = false) =>
  request<{ cards: Record<string, ReferenceCardSummary> }>(`${taskPath(id)}/reference-cards`, post({ paper_indices: paperIndices, force_refresh: forceRefresh }));
export async function deleteReview(id: string, reviewId: string): Promise<void> {
  await request(`${taskPath(id)}/reviews/${segment(reviewId)}`, remove);
}

export const fetchReviewTemplates = () => request<ReviewTemplate[]>('/review-templates');
export const fetchReviewTemplate = (id: string) => request<ReviewTemplate>(`/review-templates/${segment(id)}`);
export const createReviewTemplate = (journalName: string, papers: Array<{ title: string; content: string }>) =>
  request<ReviewTemplate>('/review-templates', post({ journal_name: journalName, papers }));
export const updateReviewTemplate = (id: string, updates: { journal_name?: string; papers?: Array<{ title: string; content: string }> }) =>
  request<ReviewTemplate>(`/review-templates/${segment(id)}`, put(updates));
export async function deleteReviewTemplate(id: string): Promise<void> {
  await request(`/review-templates/${segment(id)}`, remove);
}

export const fetchInstructionPresets = () => request<InstructionPreset[]>('/instruction-presets');
export const createInstructionPreset = (label: string, content: string) => request<InstructionPreset>('/instruction-presets', post({ label, content }));
export const updateInstructionPreset = (id: string, updates: { label?: string; content?: string }) =>
  request<InstructionPreset>(`/instruction-presets/${segment(id)}`, put(updates));
export async function deleteInstructionPreset(id: string): Promise<void> {
  await request(`/instruction-presets/${segment(id)}`, remove);
}

export const fetchPromptLog = (id: string) => request<PromptLogEntry[]>(`${taskPath(id)}/prompt-log`);
export async function deletePromptLogEntry(id: string, entryId: string): Promise<boolean> {
  const data = await request<{ ok: boolean }>(`${taskPath(id)}/prompt-log/${segment(entryId)}`, remove);
  return data.ok;
}
export async function translateReview(content: string): Promise<string> {
  const data = await request<{ translated: string }>('/translate-review', post({ content }));
  return data.translated;
}
