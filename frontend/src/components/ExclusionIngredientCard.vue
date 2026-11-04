<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue';
import { Check, ChevronDown } from 'lucide-vue-next';
import IngredientSafetyBadge from '@/components/IngredientSafetyBadge.vue';
import { getIngredient, type IngredientDetail, type IngredientSummary } from '@/services/backendApi';

const props = defineProps<{ ingredient: IngredientSummary; selected: boolean; disabled?: boolean }>();
const emit = defineEmits<{ toggle: [] }>();
const expanded = ref(false);
const detail = ref<IngredientDetail | null>(null);
const loading = ref(false);
const error = ref('');
let controller: AbortController | undefined;

async function loadDetail() {
  controller?.abort();
  const current = new AbortController();
  controller = current;
  loading.value = true;
  error.value = '';
  try {
    const result = await getIngredient(props.ingredient.id, current.signal);
    if (!current.signal.aborted) detail.value = result;
  } catch (reason) {
    if (!current.signal.aborted) error.value = reason instanceof Error ? reason.message : 'Не удалось загрузить описание';
  } finally {
    if (!current.signal.aborted) loading.value = false;
  }
}
function toggleDetails() {
  expanded.value = !expanded.value;
  if (expanded.value && !detail.value && !loading.value) void loadDetail();
}
onBeforeUnmount(() => controller?.abort());
</script>

<template>
  <article class="exclusion-card" :class="{ 'exclusion-card--selected': selected }" @click="!disabled && emit('toggle')">
    <div class="exclusion-card__header">
      <button type="button" class="exclusion-card__select" role="checkbox" :aria-checked="selected" :disabled="disabled"
        :aria-label="[ingredient.code, ingredient.name].filter(Boolean).join(' ')" @click.stop="emit('toggle')">
        <span class="exclusion-card__check"><Check v-if="selected" :size="16" aria-hidden="true" /></span>
        <span class="exclusion-card__identity">
          <strong v-if="ingredient.code" class="exclusion-card__code">{{ ingredient.code }}</strong>
          <span class="exclusion-card__name" :class="{ 'exclusion-card__name--primary': !ingredient.code }">{{ ingredient.name }}</span>
          <span v-if="selected" class="exclusion-card__selected">Исключаю</span>
        </span>
      </button>
      <button type="button" class="exclusion-card__expand" :aria-expanded="expanded" :aria-controls="`details-${ingredient.id}`"
        :aria-label="`${expanded ? 'Скрыть' : 'Подробнее о'} ${ingredient.name}`" :title="expanded ? 'Свернуть' : 'Подробнее'" @click.stop="toggleDetails">
        <ChevronDown :class="{ rotated: expanded }" aria-hidden="true" />
      </button>
    </div>
    <div v-if="expanded" :id="`details-${ingredient.id}`" class="exclusion-card__details" @click.stop>
      <p v-if="loading" role="status">Загружаем описание...</p>
      <div v-else-if="error" role="alert"><p>{{ error }}</p><button type="button" @click="loadDetail">Повторить</button></div>
      <template v-else-if="detail">
        <IngredientSafetyBadge :severity="detail.severity" />
        <p>{{ detail.full_description || detail.description || 'Описание пока отсутствует' }}</p>
        <p v-if="detail.functions.length"><strong>Функция:</strong> {{ detail.functions.join(', ') }}</p>
        <p v-if="detail.origins.length"><strong>Происхождение:</strong> {{ detail.origins.join(', ') }}</p>
        <RouterLink :to="{ name: 'ingredient', params: { id: ingredient.id } }">Полная карточка и источники</RouterLink>
      </template>
    </div>
  </article>
</template>

<style scoped>
.exclusion-card { border: 1px solid #d8e2de; border-radius: 8px; background: white; overflow: hidden; cursor: pointer; }
.exclusion-card--selected { border-color: #d67e8b; background: #fff4f5; }
.exclusion-card__header { display: grid; grid-template-columns: minmax(0, 1fr) 52px; align-items: start; }
.exclusion-card__select { display: flex; gap: 12px; min-width: 0; width: 100%; border: 0; padding: 16px; color: #17201d; background: transparent; text-align: left; font: inherit; cursor: pointer; }
.exclusion-card__check { display: grid; place-items: center; width: 22px; height: 22px; flex: 0 0 auto; margin-top: 5px; border: 1px solid #bccdc5; border-radius: 5px; }
.exclusion-card--selected .exclusion-card__check { border-color: #b42332; background: #b42332; color: white; }
.exclusion-card__identity { display: grid; gap: 6px; min-width: 0; overflow-wrap: anywhere; }
.exclusion-card__code { font-size: 28px; line-height: 1.15; color: #205b49; }
.exclusion-card__name { font-size: 14px; line-height: 1.5; font-weight: 600; }
.exclusion-card__name--primary { font-size: 18px; }
.exclusion-card__selected { font-size: 11px; color: #a52639; }
.exclusion-card__expand { display: grid; place-items: center; width: 44px; height: 44px; margin-top: 9px; border: 0; background: transparent; color: #61756c; cursor: pointer; border-radius: 6px; }
.exclusion-card__expand svg { transition: transform .15s; }
.rotated { transform: rotate(180deg); }
.exclusion-card__details { margin: 0 16px; padding: 16px 0; border-top: 1px solid #d8e2de; font-size: 13px; line-height: 1.65; color: #43534c; overflow-wrap: anywhere; cursor: auto; }
.exclusion-card__details p { margin: 10px 0; white-space: pre-line; }
.exclusion-card__details a, .exclusion-card__details button { color: #16684f; }
.exclusion-card__details button { background: transparent; border: 0; padding: 10px 0; font: inherit; cursor: pointer; }
button:focus-visible { outline: 2px solid #218060; outline-offset: -3px; }
.exclusion-card__select:disabled { opacity: .6; cursor: default; }
</style>
