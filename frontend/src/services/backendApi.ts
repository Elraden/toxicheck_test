const DEFAULT_API_BASE_URL = import.meta.env.DEV
  ? 'http://localhost:8000/api'
  : '/api';

const apiBaseUrl = (
  import.meta.env.VITE_API_BASE_URL ||
  DEFAULT_API_BASE_URL
).replace(/\/$/, '');

export type IngredientRule = {
  id: string;
  title: string;
  explanation: string | null;
  citation: string | null;
  source_title: string | null;
  source_url: string | null;
  assessment_note: string | null;
  conditions: { evidence?: Array<{
    url: string;
    locator: string;
    verification_status: string;
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

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...options,
    headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...options.headers }
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || data === null) {
    throw new Error(
      (typeof data?.detail === 'string' && data.detail) ||
      (typeof data?.message === 'string' && data.message) ||
      `Сервис недоступен (${response.status}). Повторите проверку позже.`
    );
  }
  return data as T;
}

export function analyzeIngredients(ingredientsText: string, excludedIngredientIds: string[], signal?: AbortSignal) {
  return request<IngredientAnalysis>('/analysis', {
    method: 'POST', signal,
    body: JSON.stringify({ ingredientsText, preferences: { excludedIngredientIds } })
  });
}

export function getPreferenceIngredients(signal?: AbortSignal) {
  return request<PreferenceIngredient[]>('/preferences/catalog', { signal });
}

export type CompositionScanResponse = {
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
