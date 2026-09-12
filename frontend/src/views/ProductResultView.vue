<script setup lang="ts">
import {
  computed,
  onMounted,
  ref
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

type RiskLevel =
  | 'neutral'
  | 'warning'
  | 'avoid';

type ProductRecord = Record<string, unknown>;

type IngredientItem = {
  id: string;
  name: string;
  code?: string;
  risk: RiskLevel;
};

type OcrDiagnostics = {
  status: string;
  rawText: string;
  compositionText: string;
  allergensText: string;
  confidence: string;
  processingTime: string;
};

const additiveCatalog: Record<
  string,
  {
    name: string;
    risk: RiskLevel;
  }
> = {
  E100: {
    name: 'Куркумин',
    risk: 'neutral'
  },
  E101: {
    name: 'Рибофлавин',
    risk: 'neutral'
  },
  E102: {
    name: 'Тартразин',
    risk: 'warning'
  },
  E104: {
    name: 'Хинолиновый желтый',
    risk: 'warning'
  },
  E110: {
    name: 'Желтый солнечный закат',
    risk: 'warning'
  },
  E120: {
    name: 'Кармин',
    risk: 'warning'
  },
  E122: {
    name: 'Азорубин',
    risk: 'warning'
  },
  E124: {
    name: 'Понсо 4R',
    risk: 'warning'
  },
  E129: {
    name: 'Красный очаровательный AC',
    risk: 'warning'
  },
  E171: {
    name: 'Диоксид титана',
    risk: 'avoid'
  },
  E202: {
    name: 'Сорбат калия',
    risk: 'neutral'
  },
  E211: {
    name: 'Бензоат натрия',
    risk: 'warning'
  },
  E220: {
    name: 'Диоксид серы',
    risk: 'warning'
  },
  E250: {
    name: 'Нитрит натрия',
    risk: 'warning'
  },
  E300: {
    name: 'Аскорбиновая кислота',
    risk: 'neutral'
  },
  E301: {
    name: 'Аскорбат натрия',
    risk: 'neutral'
  },
  E306: {
    name: 'Токоферолы',
    risk: 'neutral'
  },
  E322: {
    name: 'Лецитин',
    risk: 'neutral'
  },
  E330: {
    name: 'Лимонная кислота',
    risk: 'neutral'
  },
  E331: {
    name: 'Цитраты натрия',
    risk: 'neutral'
  },
  E407: {
    name: 'Каррагинан',
    risk: 'warning'
  },
  E412: {
    name: 'Гуаровая камедь',
    risk: 'neutral'
  },
  E415: {
    name: 'Ксантановая камедь',
    risk: 'neutral'
  },
  E420: {
    name: 'Сорбит',
    risk: 'warning'
  },
  E422: {
    name: 'Глицерин',
    risk: 'neutral'
  },
  E440: {
    name: 'Пектин',
    risk: 'neutral'
  },
  E450: {
    name: 'Дифосфаты',
    risk: 'warning'
  },
  E451: {
    name: 'Трифосфаты',
    risk: 'warning'
  },
  E452: {
    name: 'Полифосфаты',
    risk: 'warning'
  },
  E471: {
    name: 'Моно- и диглицериды жирных кислот',
    risk: 'neutral'
  },
  E621: {
    name: 'Глутамат натрия',
    risk: 'warning'
  },
  E950: {
    name: 'Ацесульфам калия',
    risk: 'warning'
  },
  E951: {
    name: 'Аспартам',
    risk: 'avoid'
  },
  E952: {
    name: 'Цикламаты',
    risk: 'avoid'
  },
  E955: {
    name: 'Сукралоза',
    risk: 'warning'
  },
  E960: {
    name: 'Стевиолгликозиды',
    risk: 'neutral'
  }
};

const nameHints: Array<{
  pattern: RegExp;
  code: string;
}> = [
    {
      pattern: /диоксид\s+титана/i,
      code: 'E171'
    },
    {
      pattern: /бензоат\s+натрия/i,
      code: 'E211'
    },
    {
      pattern: /кармин/i,
      code: 'E120'
    },
    {
      pattern: /аскорбинов/i,
      code: 'E300'
    },
    {
      pattern: /лимонн\w*\s+кисл/i,
      code: 'E330'
    },
    {
      pattern: /лецитин/i,
      code: 'E322'
    }
  ];

const scannerStore = useScannerStore();
const route = useRoute();
const router = useRouter();

const {
  barcode,
  productJson
} = storeToRefs(scannerStore);

const isLoading = ref(false);
const loadError = ref('');

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

function normalizeWhitespace(
  value: string
): string {
  return value
    .replace(/\s+/g, ' ')
    .trim();
}

function normalizeECode(
  value: string
): string {
  const match = value.match(
    /(?:^|[^a-zа-я])e[\s-]?(\d{3,4}[a-z]?)/i
  );

  return match?.[1]
    ? `E${match[1].toUpperCase()}`
    : '';
}

function inferCodeByName(value: string): string {
  const hint = nameHints.find((item) =>
    item.pattern.test(value)
  );

  return hint?.code ?? '';
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

function getRisk(
  code?: string
): RiskLevel {
  if (!code) {
    return 'neutral';
  }

  return additiveCatalog[code]?.risk ?? 'neutral';
}

function getIngredientName(
  rawName: string,
  code?: string
): string {
  const cleaned = normalizeWhitespace(
    rawName
      .replace(/\*/g, '')
      .replace(/\([^)]{0,80}\)/g, '')
      .replace(
        /(?:^|[^a-zа-я])e[\s-]?\d{3,4}[a-z]?/i,
        ''
      )
      .replace(/^[-:–—\s]+/, '')
      .replace(/[-:–—\s]+$/, '')
  );

  if (cleaned) {
    return cleaned;
  }

  if (code) {
    return additiveCatalog[code]?.name ?? `Добавка ${code}`;
  }

  return formatTag(rawName);
}

function createIngredientItem(
  value: string,
  index: number
): IngredientItem | null {
  const text = normalizeWhitespace(value);

  if (!text) {
    return null;
  }

  const code =
    normalizeECode(text) ||
    inferCodeByName(text);

  return {
    id: `${code ?? normalizeWhitespace(text).toLowerCase()}-${index}`,
    name: getIngredientName(text, code),
    code,
    risk: getRisk(code)
  };
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

const hasCompositionData = computed(() => {
  if (getString(product.value, 'ingredients_text')) {
    return true;
  }

  return (
    getArray(product.value, 'ingredients').length > 0 ||
    getArray(product.value, 'additives_tags').length > 0 ||
    getArray(product.value, 'additives_original_tags').length > 0
  );
});

const ingredientItems = computed<IngredientItem[]>(() => {
  if (!hasCompositionData.value) {
    return [];
  }

  const items: IngredientItem[] = [];
  const knownIngredientKeys = new Set<string>();

  const addItem = (
    item: IngredientItem | null
  ): void => {
    if (!item) {
      return;
    }

    const key =
      item.code ??
      normalizeWhitespace(item.name).toLowerCase();

    if (knownIngredientKeys.has(key)) {
      return;
    }

    knownIngredientKeys.add(key);
    items.push(item);
  };

  getArray(product.value, 'ingredients')
    .forEach((ingredient, index) => {
      if (!isRecord(ingredient)) {
        return;
      }

      const text =
        getString(ingredient, 'text') ||
        getString(ingredient, 'id');

      const code =
        normalizeECode(
          `${getString(ingredient, 'id')} ${text}`
        ) ||
        inferCodeByName(text);

      addItem({
        id: `${code ?? normalizeWhitespace(text).toLowerCase()}-structured-${index}`,
        name: getIngredientName(text, code),
        code,
        risk: getRisk(code)
      });
    });

  [
    ...getArray(product.value, 'additives_tags'),
    ...getArray(product.value, 'additives_original_tags')
  ].forEach((tag, index) => {
    if (typeof tag !== 'string') {
      return;
    }

    const code = normalizeECode(tag);

    if (!code) {
      return;
    }

    addItem({
      id: `${code}-tag-${index}`,
      name: additiveCatalog[code]?.name ?? formatTag(tag),
      code,
      risk: getRisk(code)
    });
  });

  getString(product.value, 'ingredients_text')
    .split(/[,;]/)
    .forEach((part, index) => {
      addItem(
        createIngredientItem(part, index)
      );
    });

  return items;
});

const riskLevel = computed<RiskLevel>(() => {
  if (
    ingredientItems.value.some((item) =>
      item.risk === 'avoid'
    )
  ) {
    return 'avoid';
  }

  if (
    ingredientItems.value.some((item) =>
      item.risk === 'warning'
    )
  ) {
    return 'warning';
  }

  return 'neutral';
});

const resultTitle = computed(() => {
  return riskLevel.value === 'neutral'
    ? 'Без явных рисков'
    : 'С осторожностью';
});

const resultDescription = computed(() => {
  if (riskLevel.value === 'neutral') {
    return 'В составе не найдено добавок из списка повышенного внимания.';
  }

  return 'Продукт содержит добавки, которые могут вызвать нежелательную реакцию при регулярном употреблении.';
});

function getRiskLabel(
  level: RiskLevel
): string {
  if (level === 'avoid') {
    return 'Нежелательный';
  }

  if (level === 'warning') {
    return 'Внимание';
  }

  return 'Нейтральный';
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

async function ensureProductLoaded():
  Promise<void> {
  if (productJson.value !== null) {
    return;
  }

  const code = productCode.value;

  if (!code) {
    return;
  }

  isLoading.value = true;
  loadError.value = '';

  try {
    const data =
      await getProductByBarcode(code);

    scannerStore.setBarcode(code);
    scannerStore.setProductJson(data);
  } catch (error) {
    loadError.value =
      error instanceof Error
        ? error.message
        : 'Не удалось загрузить товар';
  } finally {
    isLoading.value = false;
  }
}

onMounted(() => {
  void ensureProductLoaded();
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

        <section v-if="isProductMissing" class="result-state result-state--empty">
          Товар не найден в базе Open Food Facts
        </section>

        <section v-if="!isProductMissing && hasCompositionData" class="risk-card" :class="`risk-card--${riskLevel}`">
          <div class="risk-card__title">
            <AlertTriangle aria-hidden="true" />
            <h2>{{ resultTitle }}</h2>
          </div>

          <p class="risk-card__text">
            {{ resultDescription }}
          </p>

          <button type="button" class="risk-card__details">
            <Info aria-hidden="true" />
            <span>Почему такой результат?</span>
            <ChevronRight aria-hidden="true" />
          </button>
        </section>

        <section v-if="!isProductMissing && hasCompositionData" class="composition">
          <h2 class="composition__title">
            Состав продукта
          </h2>

          <div v-if="ingredientItems.length" class="composition__list">
            <article v-for="item in ingredientItems" :key="item.id" class="ingredient-card">
              <div class="ingredient-card__main">
                <h3 class="ingredient-card__name">
                  {{ item.name }}
                </h3>

                <span v-if="item.code" class="ingredient-card__code">
                  {{ item.code }}
                </span>
              </div>

              <span class="ingredient-card__badge" :class="`ingredient-card__badge--${item.risk}`">
                {{ getRiskLabel(item.risk) }}
              </span>

              <ChevronRight class="ingredient-card__arrow" aria-hidden="true" />
            </article>
          </div>

          <p v-else class="composition__empty">
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
  overflow: hidden;
  margin: 0;
  color: #0d1714;
  font-size: 18px;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  grid-template-columns: 1fr 1fr;
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
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto 24px;
  align-items: center;
  gap: 10px;
  min-height: 48px;
  padding: 10px 12px;
}

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
  max-width: 112px;
  padding: 5px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  white-space: nowrap;
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
}
</style>
