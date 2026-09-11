const DEFAULT_API_BASE_URL = import.meta.env.DEV
  ? 'http://localhost:8000/api'
  : '/api';

const apiBaseUrl = (
  import.meta.env.VITE_API_BASE_URL ||
  DEFAULT_API_BASE_URL
).replace(/\/$/, '');

export type CompositionScanResponse = {
  jobId?: string | null;
  status: string;
  message: string;
  recognizedText?: string | null;
  ingredientsText?: string | null;
  allergensText?: string | null;
  confidence?: number | null;
  processingTimeMs?: number | null;
};

export async function scanCompositionImage(
  imageBase64: string
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
        imageBase64
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
