import { apiBaseUrl, request } from './http';
import { csrfHeaders, ensureGuestSession } from './userSession';

export type IngredientRule = {
  id: string;
  title: string;
  explanation: string | null;
  citation: string | null;
  source_title: string | null;
  source_url: string | null;
  assessment_note: string | null;
  conditions: { regulatory_status?: string; verification_status?: string; primary_basis_required?: boolean; production_ready?: boolean | null; evidence?: Array<{
    url: string;
    locator: string;
    verification_status: string;
    source_title?: string;
  }> };
};

export type MatchedIngredient = {
  ingredient_id: string;
  name: string;
  code: string | null;
  raw_text: string;
  matched_by: string;
  severity: string;
  rules: IngredientRule[];
};

export type IngredientAnalysis = {
  tokens?: string[];
  matched: MatchedIngredient[];
  unmatched: string[];
  verdict: {
    level: string;
    title: string;
    description: string;
    reasons: Array<{
      title: string;
      explanation: string | null;
      ingredient_id: string | null;
      severity: string;
    }>;
  };
};

export type PreferenceIngredient = {
  id: string;
  name: string;
  code: string | null;
  category: string;
  description: string;
  legacy_ids: string[];
};

export function analyzeIngredients(ingredientsText: string, excludedIngredientIds: string[], signal?: AbortSignal) {
  return request<IngredientAnalysis>('/analysis', {
    method: 'POST', signal,
    body: JSON.stringify({ ingredientsText, preferences: { excludedIngredientIds } })
  });
}

export async function getSelectedPreferenceIngredients(ids: string[], signal?: AbortSignal) {
  const result: PreferenceIngredient[] = [];
  const unique = [...new Set(ids)];
  for (let offset = 0; offset < unique.length; offset += 100) {
    const params = new URLSearchParams();
    unique.slice(offset, offset + 100).forEach(id => params.append('ids', id));
    result.push(...await request<PreferenceIngredient[]>(`/preferences/catalog?${params}`, { signal }));
  }
  return result;
}

export type IngredientSummary = {
  id: string;
  name: string;
  code: string | null;
  category: string | null;
  severity: string;
};

export type IngredientPage = {
  items: IngredientSummary[];
  total: number;
  limit: number;
  offset: number;
};

export type IngredientDetail = IngredientSummary & {
  name_en: string | null;
  description: string | null;
  full_description: string | null;
  functions: string[];
  origins: string[];
  aliases: string[];
  rules: IngredientRule[];
};

export function getIngredients(q: string, kind: string, offset: number, signal?: AbortSignal, status = 'all') {
  const params = new URLSearchParams({ q, kind, status, offset: String(offset), limit: '30' });
  return request<IngredientPage>(`/ingredients?${params}`, { signal });
}

export function getIngredient(id: string, signal?: AbortSignal) {
  return request<IngredientDetail>(`/ingredients/${encodeURIComponent(id)}`, { signal });
}

export type CompositionScanResponse = {
  requestId?: string | null;
  tokens?: string[];
  jobId?: string | null;
  status: string;
  captureSource?: string | null;
  message: string;
  recognizedText?: string | null;
  ingredientsText?: string | null;
  allergensText?: string | null;
  confidence?: number | null;
  processingTimeMs?: number | null;
};

export type IngredientCorrection = {
  id: string;
  tokenIndex: number;
  tokenRaw: string;
  correctedText: string;
};

export type CorrectionsResponse = {
  saved: number;
  status: string;
  ingredientsText: string;
  analysis: IngredientAnalysis;
};

export async function saveIngredientCorrections(requestId: string, ingredientsText: string,
  corrections: IngredientCorrection[], excludedIngredientIds: string[]) {
  const session = await ensureGuestSession();
  return request<CorrectionsResponse>('/scan/corrections', {
    method: 'POST',
    headers: csrfHeaders(session.csrf),
    body: JSON.stringify({ requestId, ingredientsText, corrections, preferences: { excludedIngredientIds } })
  });
}

export async function scanCompositionImage(
  imageBase64: string,
  captureSource = 'unknown'
): Promise<CompositionScanResponse> {
  const response = await fetch(
    `${apiBaseUrl}/scan/composition`,
    {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        imageBase64,
        captureSource
      })
    }
  );

  const data =
    await response.json() as CompositionScanResponse;

  if (!response.ok) {
    throw new Error(
      data.message ||
      `Backend вернул ошибку ${response.status}`
    );
  }

  return data;
}
