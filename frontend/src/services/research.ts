import { request, apiBaseUrl } from './http';
import { csrfHeaders, ensureGuestSession, refreshSession } from './userSession';
import type { IngredientAnalysis } from './backendApi';

export type Study = { id: string; title: string; instruction: string; scenario: 'comparison' | 'ocr_quality'; protocol_version: string; is_open: boolean };
export type Answers = Record<string, 'present' | 'absent' | 'unknown'>;
export type Photo = { id: string; mime_type: string; sha256: string; size_bytes: number; uploaded_at: string };
export type Attempt = {
  id: string; photo_id: string; status: string; error: string | null; app_e2e_ms: number | null;
  server_processing_ms: number | null; displayed_at: string | null; started_at: string;
  app_version: string; pipeline_version: string; catalog_version: string;
  result?: { raw_ingredient_text: string | null; ingredients_text: string | null; analysis: IngredientAnalysis | null;
    app_detected_e_codes: string[]; matched_restrictions: string[] } | null;
};
export type Validation = { revision: number; status: string; verified_label_text: string; ground_truth_e_codes: string[]; comment: string; reviewer_id: string | null; updated_at: string };
export type ResearchRun = {
  id: string; study_id: string; participant_id: string; case_id: string; scenario: string; stage: string;
  revision: number; instruction: string; protocol_version: string; created_at: string;
  exclusions: { ingredient_id: string; e_code: string }[];
  manual_answers?: Answers; app_answers?: Answers; external_search_used: boolean | null;
  manual_started_at: string | null; app_started_at: string | null;
  manual_duration_ms: number | null; app_duration_ms: number | null; selected_attempt_id: string | null;
  user_label_text: string | null; labeling_status: string; error_reason: string | null;
  photos: Photo[]; attempts: Attempt[]; validation?: Validation; audit?: { id: number; event: string; created_at: string; actor_id: string; details: unknown }[];
  case_title?: string; study_title?: string;
};
export type RunSummary = Pick<ResearchRun, 'id' | 'stage' | 'scenario' | 'created_at'> & {
  title?: string; case_title?: string; study_title: string; participant_id?: string; case_id?: string; validation_status?: string; photo_count?: number;
};

export async function researchWrite<T>(path: string, body: unknown = {}, admin = false, method = 'POST'): Promise<T> {
  const state = admin ? await refreshSession() : await ensureGuestSession();
  return request<T>((admin ? '/admin/research' : '/research') + path,
    { method, headers: csrfHeaders(admin ? state.admin_csrf : state.csrf), body: JSON.stringify(body) });
}

export function photoUrl(runId: string, photoId: string, admin = false) {
  return `${apiBaseUrl}/${admin ? 'admin/research' : 'research'}/runs/${runId}/photos/${photoId}`;
}

export const stageNames: Record<string, string> = {
  manual_ready: 'Перед ручной проверкой', manual_active: 'Ручная проверка', app_ready: 'Перед ToxiCheck',
  app_active: 'Проверка ToxiCheck', labeling: 'Разметка состава', completed: 'Завершено', aborted: 'Прервано'
};
export const validationNames: Record<string, string> = {
  unreviewed: 'Не проверено', confirmed: 'Подтверждено', clarification: 'Требуется уточнение', unverifiable: 'Невозможно проверить'
};
export const answerNames = { present: 'Присутствует', absent: 'Отсутствует', unknown: 'Не удалось определить' };
export const attemptNames: Record<string, string> = { processing: 'Обрабатывается', success: 'Готово', needs_retake: 'Нужен повторный снимок', error: 'Ошибка', interrupted: 'Прервано' };
export function duration(ms: number | null | undefined) { return ms == null ? 'Нет замера' : `${(ms / 1000).toFixed(1)} с`; }

export function fileData(file: File): Promise<string> {
  if (file.size > 12 * 1024 * 1024) return Promise.reject(new Error('Выберите фото размером до 12 МБ.'));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error('Не удалось прочитать фото.'));
    reader.readAsDataURL(file);
  });
}
