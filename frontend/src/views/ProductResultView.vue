<script setup lang="ts">
import {
  computed,
  onBeforeUnmount,
  ref,
  watch
} from 'vue';

import { storeToRefs } from 'pinia';
import {
  useRoute,
  useRouter
} from 'vue-router';

import {
  AlertTriangle,
  ArrowLeft,
  Bookmark,
  ChevronRight,
  Info
} from 'lucide-vue-next';

import { getProductByBarcode } from '@/services/openFoodFacts';
import { useScannerStore } from '@/stores/scanner';
import { analyzeIngredients, type IngredientAnalysis, type IngredientRule } from '@/services/backendApi';
import { readPersonalExclusions } from '@/services/personalExclusions';

type RiskLevel =
  | 'unknown'
  | 'neutral'
  | 'warning'
  | 'avoid';

type ProductRecord = Record<string, unknown>;

type IngredientItem = {
  id: string;
  name: string;
  code?: string;
  risk: RiskLevel;
  rules: IngredientRule[];
  matchedBy?: string;
  rawText?: string;
};

type OcrDiagnostics = {
  status: string;
  source: string;
  rawText: string;
  compositionText: string;
  allergensText: string;
  confidence: string;
  processingTime: string;
};

const scannerStore = useScannerStore();
const route = useRoute();
const router = useRouter();

const {
  barcode,
  productJson
} = storeToRefs(scannerStore);

const isLoading = ref(false);
const loadError = ref('');
const analysis = ref<IngredientAnalysis | null>(null);
const analysisLoading = ref(false);
const analysisError = ref('');
const showReasons = ref(false);
let analysisController: AbortController | undefined;
let productRequestId = 0;

function isRecord(
  value: unknown
): value is ProductRecord {
  return (
    typeof value === 'object' &&
    value !== null &&
    !Array.isArray(value)
  );
}

function getString(
  source: ProductRecord | null,
  key: string
): string {
  const value = source?.[key];

  return typeof value === 'string'
    ? value.trim()
    : '';
}

function getArray(
  source: ProductRecord | null,
  key: string
): unknown[] {
  const value = source?.[key];

  return Array.isArray(value)
    ? value
    : [];
}

function getNumber(
  source: ProductRecord | null,
  key: string
): number | null {
  const value = source?.[key];

  return typeof value === 'number'
    ? value
    : null;
}

function formatTag(value: string): string {
  const withoutLanguage = value.replace(
    /^[a-z]{2}:/i,
    ''
  );

  return withoutLanguage
    .replace(/-/g, ' ')
    .replace(/\b\w/g, (letter) =>
      letter.toUpperCase()
    );
}

function getRouteBarcode(): string {
  const rawBarcode = route.params.barcode;

  if (Array.isArray(rawBarcode)) {
    return rawBarcode[0] ?? '';
  }

  return rawBarcode ?? '';
}

const responseRoot = computed<ProductRecord | null>(() => {
  return isRecord(productJson.value)
    ? productJson.value
    : null;
});

const product = computed<ProductRecord | null>(() => {
  const root = responseRoot.value;

  if (isRecord(root?.product)) {
    return root.product;
  }

  return root;
});

const isProductMissing = computed(() => {
  const root = responseRoot.value;

  if (!root || isRecord(root.product)) {
    return false;
  }

  const result = isRecord(root.result)
    ? root.result
    : null;

  const resultId =
    getString(result, 'id').toLowerCase();

  const status =
    getString(root, 'status').toLowerCase();

  return (
    status === 'failure' ||
    status === 'not_found' ||
    resultId.includes('not_found') ||
    (
      getArray(root, 'errors').length > 0 &&
      Boolean(
        getString(root, 'code') ||
        barcode.value ||
        getRouteBarcode()
      )
    )
  );
});

const productCode = computed(() => {
  return (
    getRouteBarcode() ||
    barcode.value ||
    getString(product.value, 'code') ||
    getString(responseRoot.value, 'code')
  );
});

const productName = computed(() => {
  if (isProductMissing.value) {
    return 'Товар не найден';
  }

  return (
    getString(product.value, 'product_name') ||
    getString(product.value, 'generic_name') ||
    'Продукт без названия'
  );
});

const productBrand = computed(() => {
  return getString(product.value, 'brands');
});

const productCategory = computed(() => {
  const categories = getString(
    product.value,
    'categories'
  );

  if (categories) {
    return categories
      .split(',')
      .map((item) => item.trim())
      .find(Boolean) ?? '';
  }

  const [firstCategory] = getArray(
    product.value,
    'categories_tags'
  );

  return typeof firstCategory === 'string'
    ? formatTag(firstCategory)
    : '';
});

const productQuantity = computed(() => {
  return getString(product.value, 'quantity');
});

const productMeta = computed(() => {
  return [
    productBrand.value,
    productCategory.value,
    productQuantity.value
  ].filter(Boolean);
});

const productFallbackMeta = computed(() => {
  return productCode.value
    ? `Штрихкод ${productCode.value}`
    : 'Скан состава';
});

const ocrDiagnostics = computed<OcrDiagnostics | null>(() => {
  const root = responseRoot.value;
  const ocr = isRecord(root?.ocr)
    ? root.ocr
    : null;

  if (!ocr) {
    return null;
  }

  const confidence =
    getNumber(ocr, 'confidence');
  const processingTime =
    getNumber(ocr, 'processing_time_ms');

  return {
    status: getString(ocr, 'status') || 'success',
    source: getString(ocr, 'capture_source') || 'unknown',
    rawText: getString(ocr, 'raw_text'),
    compositionText:
      getString(ocr, 'composition_text') ||
      getString(product.value, 'ingredients_text'),
    allergensText:
      getString(ocr, 'allergens_text') ||
      getString(product.value, 'allergens'),
    confidence: confidence === null
      ? '—'
      : `${Math.round(confidence * 100)}%`,
    processingTime: processingTime === null
      ? '—'
      : `${processingTime} мс`
  };
});

const compositionText = computed(() => {
  const raw = getString(product.value, 'ingredients_text');
  if (raw) return raw;
  const names: string[] = [];
  const visit = (items: unknown[]) => {
    for (const item of items) {
      if (!isRecord(item)) continue;
      const name = getString(item, 'text') || getString(item, 'id').replace(/^[a-z]{2}:/i, '');
      if (name) names.push(name);
      visit(getArray(item, 'ingredients'));
    }
  };
  visit(getArray(product.value, 'ingredients'));
  return names.join(', ');
});

const hasCompositionData = computed(() => Boolean(compositionText.value));
const ingredientItems = computed<IngredientItem[]>(() => {
  if (!analysis.value) return [];
  const personal = new Set(analysis.value.verdict.reasons
    .filter((reason) => reason.severity === 'personal').map((reason) => reason.ingredient_id));
  return [
    ...analysis.value.matched.map((item): IngredientItem => ({
      id: item.ingredient_id, name: item.name, code: item.code || undefined,
      rawText: item.raw_text, matchedBy: item.matched_by, rules: item.rules,
      risk: personal.has(item.ingredient_id) ? 'warning' :
        ['avoid', 'forbidden'].includes(item.severity) ? 'avoid' :
        item.severity === 'attention' ? 'warning' :
        item.severity === 'unknown' || !item.rules.length ? 'unknown' : 'neutral'
    })),
    ...analysis.value.unmatched.map((name, index): IngredientItem => ({
      id: `unmatched-${index}`, name, risk: 'unknown', rules: []
    }))
  ];
});
const riskLevel = computed<RiskLevel>(() => {
  const level = analysis.value?.verdict.level;
  if (level === 'dangerous') return 'avoid';
  if (level === 'attention' || level === 'risky_for_user') return 'warning';
  return 'unknown';
});
const resultTitle = computed(() => analysis.value?.verdict.title || '');
const resultDescription = computed(() => analysis.value?.verdict.description || '');

function getRiskLabel(level: RiskLevel): string {
  return { unknown: 'Нет данных', neutral: 'Нет предупреждений', warning: 'Внимание', avoid: 'Ограничения' }[level];
}

function sourceUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined;
  } catch { return undefined; }
}

async function runAnalysis(): Promise<void> {
  analysisController?.abort();
  const controller = new AbortController();
  analysisController = controller;
  analysis.value = null;
  analysisError.value = '';
  analysisLoading.value = false;
  showReasons.value = false;
  if (isProductMissing.value || !compositionText.value) return;
  analysisLoading.value = true;
  try {
    const result = await analyzeIngredients(compositionText.value, readPersonalExclusions(), controller.signal);
    if (!controller.signal.aborted) analysis.value = result;
  } catch (error) {
    if (!controller.signal.aborted) {
      analysisError.value = error instanceof Error ? error.message : 'Не удалось проверить состав';
    }
  } finally {
    if (!controller.signal.aborted) analysisLoading.value = false;
  }
}

function goBack(): void {
  if (window.history.length > 1) {
    router.back();
    return;
  }

  void router.push({
    name: 'scan'
  });
}

async function ensureProductLoaded(): Promise<void> {
  const requestId = ++productRequestId;
  isLoading.value = false;
  const code = getRouteBarcode();
  const root = isRecord(productJson.value) ? productJson.value : null;
  const storedProduct = isRecord(root?.product) ? root.product : root;
  const storedCode = getString(storedProduct, 'code') || getString(root, 'code');
  if (root && (!code || code === storedCode)) return;
  scannerStore.setProductJson(null);
  loadError.value = '';
  if (!code) return;
  isLoading.value = true;
  try {
    const data = await getProductByBarcode(code);
    if (requestId !== productRequestId) return;
    scannerStore.setBarcode(code);
    scannerStore.setProductJson(data);
  } catch (error) {
    if (requestId === productRequestId) {
      loadError.value = error instanceof Error ? error.message : 'Не удалось загрузить товар';
    }
  } finally {
    if (requestId === productRequestId) isLoading.value = false;
  }
}

watch(() => route.params.barcode, () => { void ensureProductLoaded(); }, { immediate: true });
watch([compositionText, isProductMissing], () => { void runAnalysis(); }, { immediate: true });
onBeforeUnmount(() => {
  productRequestId++;
  analysisController?.abort();
});
</script>

<template>
  <section class="result-screen">
    <header class="result-header">
      <button type="button" class="result-header__button" aria-label="Назад" @click="goBack">
        <ArrowLeft aria-hidden="true" />
      </button>

      <h1 class="result-header__title">
        Результат проверки
      </h1>

      <button type="button" class="result-header__button" aria-label="Сохранить">
        <Bookmark aria-hidden="true" />
      </button>
    </header>

    <main class="result-content">
      <div v-if="isLoading" class="result-state">
        Загружаем результат...
      </div>

      <div v-else-if="loadError" class="result-state result-state--error">
        {{ loadError }}
      </div>

      <div v-else-if="!product" class="result-state">
        Результат сканирования пока пуст
      </div>

      <template v-else>
        <section class="product-card">
          <h2 class="product-card__title">
            {{ productName }}
          </h2>

          <p class="product-card__meta">
            <template v-if="productMeta.length">
              {{ productMeta.join(' • ') }}
            </template>

            <template v-else>
              {{ productFallbackMeta }}
            </template>
          </p>
        </section>

        <section v-if="ocrDiagnostics" class="ocr-debug">
          <div class="ocr-debug__header">
            <h2 class="ocr-debug__title">
              Диагностика OCR
            </h2>

            <span class="ocr-debug__status">
              {{ ocrDiagnostics.status }}
            </span>
          </div>

          <dl class="ocr-debug__meta">
            <div>
              <dt>Источник</dt>
              <dd>{{ ocrDiagnostics.source }}</dd>
            </div>

            <div>
              <dt>Confidence</dt>
              <dd>{{ ocrDiagnostics.confidence }}</dd>
            </div>

            <div>
              <dt>Время</dt>
              <dd>{{ ocrDiagnostics.processingTime }}</dd>
            </div>
          </dl>

          <div class="ocr-debug__block">
            <h3>Очищенный состав</h3>
            <pre>{{ ocrDiagnostics.compositionText || 'Состав не определен' }}</pre>
          </div>

          <div v-if="ocrDiagnostics.allergensText" class="ocr-debug__block">
            <h3>Аллергены</h3>
            <pre>{{ ocrDiagnostics.allergensText }}</pre>
          </div>

          <div class="ocr-debug__block">
            <h3>Сырой ответ OCR</h3>
            <pre>{{ ocrDiagnostics.rawText || 'OCR не вернул текст' }}</pre>
          </div>
        </section>

        <div v-if="analysisLoading" class="result-state" role="status">Проверяем состав по справочнику...</div>
        <div v-else-if="analysisError" class="result-state result-state--error" role="alert">
          <p>{{ analysisError }}</p>
          <button type="button" class="risk-card__details" @click="runAnalysis">Повторить проверку</button>
        </div>

        <section v-if="isProductMissing" class="result-state result-state--empty">
          Товар не найден в базе Open Food Facts
        </section>

        <section v-if="!isProductMissing && analysis" class="risk-card" :class="`risk-card--${riskLevel}`">
          <div class="risk-card__title">
            <AlertTriangle aria-hidden="true" />
            <h2>{{ resultTitle }}</h2>
          </div>

          <p class="risk-card__text">
            {{ resultDescription }}
          </p>

          <button type="button" class="risk-card__details" :aria-expanded="showReasons" @click="showReasons = !showReasons">
            <Info aria-hidden="true" />
            <span>Почему такой результат?</span>
            <ChevronRight aria-hidden="true" />
          </button>
        </section>

        <div v-if="showReasons && analysis" class="verdict-reasons">
          <p v-for="(reason, index) in analysis.verdict.reasons" :key="index">
            <strong>{{ reason.title }}</strong><br>{{ reason.explanation }}
          </p>
          <p v-if="!analysis.verdict.reasons.length">{{ analysis.verdict.description }}</p>
        </div>

        <section v-if="!isProductMissing && hasCompositionData" class="composition">
          <h2 class="composition__title">
            Состав продукта
          </h2>

          <div v-if="ingredientItems.length" class="composition__list">
            <article v-for="item in ingredientItems" :key="item.id" class="ingredient-card">
              <div class="ingredient-card__main">
                <h3 class="ingredient-card__name">
                  <RouterLink v-if="item.matchedBy" :to="{ name: 'ingredient', params: { id: item.id } }" class="ingredient-card__link">
                    {{ item.name }}
                    <ChevronRight class="ingredient-card__arrow" aria-hidden="true" />
                  </RouterLink>
                  <template v-else>{{ item.name }}</template>
                </h3>

                <span v-if="item.code" class="ingredient-card__code">
                  {{ item.code }}
                </span>
              </div>

              <span class="ingredient-card__badge" :class="`ingredient-card__badge--${item.risk}`">
                {{ item.risk === 'unknown' && item.matchedBy ? 'Оценки нет' : getRiskLabel(item.risk) }}
              </span>


              <details v-if="item.rules.length" class="ingredient-evidence">
                <summary>Правила и источники ({{ item.rules.length }})</summary>
                <p v-if="item.matchedBy === 'similarity'">Приблизительное совпадение: {{ item.rawText }}</p>
                <div v-for="rule in item.rules" :key="rule.id" class="ingredient-evidence__rule">
                  <h4>{{ rule.title }}</h4>
                  <p>{{ rule.assessment_note || rule.explanation }}</p>
                  <template v-if="rule.conditions.evidence?.length">
                    <p v-for="(evidence, index) in rule.conditions.evidence" :key="index">
                      <a v-if="sourceUrl(evidence.url)" :href="sourceUrl(evidence.url)" target="_blank" rel="noopener noreferrer">{{ evidence.locator }}</a>
                      <span v-else>{{ evidence.locator }}</span>
                      <small v-if="evidence.verification_status !== 'verified_primary'">Нормативное основание требует проверки</small>
                    </p>
                  </template>
                  <p v-else>
                    {{ rule.citation }}
                    <a v-if="sourceUrl(rule.source_url)" :href="sourceUrl(rule.source_url)" target="_blank" rel="noopener noreferrer">{{ rule.source_title || 'Источник' }}</a>
                  </p>
                </div>
              </details>
            </article>
          </div>

          <p v-else-if="!analysisLoading && !analysisError" class="composition__empty">
            В составе нет распознанных ингредиентов.
          </p>
        </section>

        <p v-else-if="!isProductMissing" class="composition__empty">
          Состав не определен
        </p>
      </template>
    </main>
  </section>
</template>

<style scoped>
.result-screen {
  min-height: calc(100dvh - 72px);
  background-color: #f6f8f7;
}

.result-header {
  position: sticky;
  top: 0;
  z-index: 20;
  display: grid;
  grid-template-columns: 40px 1fr 40px;
  align-items: center;
  gap: 8px;
  min-height: 56px;
  padding: 8px 16px;
  border-bottom: 1px solid #dfe6e3;
  background-color: #ffffff;
}

.result-header__button {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border: 0;
  border-radius: 8px;
  color: #17201d;
  background: transparent;
  cursor: pointer;
}

.result-header__button svg {
  width: 22px;
  height: 22px;
  stroke-width: 2;
}

.result-header__title {
  overflow-wrap: anywhere;
  margin: 0;
  color: #0d1714;
  font-size: 18px;
  line-height: 1.2;
}

.result-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 18px 24px 24px;
}

.result-state {
  padding: 18px;
  border: 1px solid #dfe6e3;
  border-radius: 12px;
  color: #66736e;
  background-color: #ffffff;
  text-align: center;
}

.result-state--error {
  color: #b42318;
  border-color: #f3b7ae;
  background-color: #fff4f2;
}

.product-card,
.risk-card,
.ocr-debug,
.ingredient-card,
.composition__empty {
  border: 1px solid #dfe6e3;
  border-radius: 12px;
  background-color: #ffffff;
}

.product-card {
  padding: 16px;
}

.product-card__title {
  margin: 0 0 8px;
  color: #0d1714;
  font-size: 16px;
  line-height: 1.3;
}

.product-card__meta {
  margin: 0;
  color: #66736e;
  font-size: 12px;
  line-height: 1.4;
}

.ocr-debug {
  padding: 14px;
}

.ocr-debug__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.ocr-debug__title {
  margin: 0;
  color: #0d1714;
  font-size: 14px;
  line-height: 1.3;
}

.ocr-debug__status {
  max-width: 120px;
  padding: 5px 9px;
  border-radius: 999px;
  color: #66736e;
  background-color: #eef4f1;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  text-align: center;
}

.ocr-debug__meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin: 0 0 12px;
}

.ocr-debug__meta div {
  min-width: 0;
  padding: 10px;
  border-radius: 8px;
  background-color: #f6f8f7;
}

.ocr-debug__meta dt {
  margin: 0 0 4px;
  color: #6f7d78;
  font-size: 11px;
}

.ocr-debug__meta dd {
  margin: 0;
  color: #0d1714;
  font-size: 13px;
  font-weight: 700;
}

.ocr-debug__block {
  margin-top: 12px;
}

.ocr-debug__block h3 {
  margin: 0 0 6px;
  color: #24302c;
  font-size: 12px;
  line-height: 1.3;
}

.ocr-debug__block pre {
  overflow: auto;
  max-height: 180px;
  margin: 0;
  padding: 10px;
  border-radius: 8px;
  color: #dbe7e2;
  background-color: #14201c;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}

.risk-card {
  padding: 18px 16px;
}

.risk-card--neutral {
  border-color: #9bdcc2;
  background-color: #effaf6;
}

.risk-card--warning,
.risk-card--avoid {
  border-color: #facb7a;
  background-color: #fff8e8;
}

.risk-card__title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #ff9900;
}

.risk-card--neutral .risk-card__title {
  color: #20b883;
}

.risk-card__title svg {
  flex: 0 0 auto;
  width: 18px;
  height: 18px;
  stroke-width: 2.2;
}

.risk-card__title h2 {
  margin: 0;
  font-size: 16px;
  line-height: 1.25;
}

.risk-card__text {
  margin: 14px 0;
  color: #24302c;
  font-size: 14px;
  line-height: 1.55;
}

.risk-card__details {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  border: 0;
  color: #20b883;
  background: transparent;
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.risk-card__details svg {
  width: 16px;
  height: 16px;
  stroke-width: 2.2;
}

.composition {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.composition__title {
  margin: 0;
  color: #0d1714;
  font-size: 14px;
  line-height: 1.3;
}

.composition__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.ingredient-card {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  min-height: 48px;
  padding: 10px 32px 10px 12px;
}

.ingredient-card__link { color: inherit; text-decoration: none; }
.ingredient-card__link::after { content: ''; position: absolute; inset: 0; border-radius: 12px; }
.ingredient-card__link:focus-visible { outline: none; }
.ingredient-card__link:focus-visible::after { outline: 2px solid #25a777; outline-offset: 2px; }
.ingredient-card__link .ingredient-card__arrow { position: absolute; right: 10px; top: 16px; }
.ingredient-evidence { position: relative; z-index: 1; }

.ingredient-card__main {
  min-width: 0;
}

.ingredient-card__name {
  display: inline;
  margin: 0;
  color: #0d1714;
  font-size: 14px;
  line-height: 1.35;
}

.ingredient-card__code {
  margin-left: 6px;
  color: #6f7d78;
  font-size: 11px;
  white-space: nowrap;
}

.ingredient-card__badge {
  max-width: 140px;
  padding: 5px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.3;
  white-space: normal;
}

.ingredient-card__badge--neutral {
  color: #6f7d78;
  background-color: #eef4f1;
}

.ingredient-card__badge--warning {
  color: #ff9900;
  background-color: #fff1d6;
}

.ingredient-card__badge--avoid {
  color: #ef4444;
  background-color: #ffe9e8;
}

.ingredient-card__arrow {
  justify-self: end;
  width: 18px;
  height: 18px;
  color: #9ca8a4;
  stroke-width: 2.2;
}

.composition__empty {
  padding: 16px;
  color: #66736e;
  font-size: 13px;
  line-height: 1.5;
}

@media (max-width: 360px) {
  .result-content {
    padding-right: 16px;
    padding-left: 16px;
  }

  .ingredient-card {
    grid-template-columns: minmax(0, 1fr) 24px;
  }

  .ingredient-card__badge {
    grid-column: 1 / -1;
    width: fit-content;
  }

  .ocr-debug__meta {
    grid-template-columns: 1fr;
  }
}
.ingredient-evidence { grid-column: 1 / -1; min-width: 0; font-size: 13px; line-height: 1.5; }
.ingredient-evidence[open] { max-height: 420px; overflow-y: auto; overscroll-behavior: contain; }
.ingredient-evidence summary { cursor: pointer; color: #16875f; padding: 8px 0; }
.ingredient-evidence__rule { border-top: 1px solid #dfe6e3; padding: 8px 0; }
.ingredient-evidence h4 { margin: 4px 0; font-size: 13px; }
.ingredient-evidence p { margin: 8px 0; }
.ingredient-evidence a { color: #167759; }
.ingredient-evidence small { display: block; color: #66736e; }
.ingredient-evidence, .product-card, .ingredient-card__name, .ocr-debug__meta dd { overflow-wrap: anywhere; }
.ingredient-card__badge--unknown { color: #53616a; background: #edf0f2; }
.risk-card--unknown { background: #f0f3f5; border-color: #cdd5da; }
.risk-card--unknown .risk-card__title { color: #53616a; }
.verdict-reasons { font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
</style>
