<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { Pencil, Save } from 'lucide-vue-next';
import { getIngredients, saveIngredientCorrections, type CorrectionsResponse, type IngredientCorrection, type IngredientSummary } from '@/services/backendApi';
import { personalExclusions } from '@/services/personalExclusions';

const props = defineProps<{
  requestId: string;
  composition: string;
  tokens: string[];
  unmatched: string[];
  uncertain?: boolean;
}>();
const emit = defineEmits<{ corrected: [result: CorrectionsResponse] }>();
const editAll = ref(false);
const confirmed = ref(false);
const drafts = ref<Record<number, string>>({});
const ids = new Map<number, { text: string; id: string }>();
const saving = ref(false);
const error = ref('');
const activeRow = ref<number | null>(null);
const suggestions = ref<IngredientSummary[]>([]);
let timer: ReturnType<typeof setTimeout> | undefined;
let controller: AbortController | undefined;
let alive = true;
const rows = computed(() => (props.tokens.length ? props.tokens : ['']).map((raw, index) => ({ raw, index }))
  .filter((row) => props.uncertain || editAll.value || !row.raw || props.unmatched.includes(row.raw)));
const changes = computed(() => (props.tokens.length ? props.tokens : ['']).map((raw, index) => ({ raw, index }))
  .filter((row) => drafts.value[row.index]?.trim()));
const listId = computed(() => `corrections-${props.requestId}`);

watch(() => props.composition, () => {
  drafts.value = {};
  ids.clear();
  suggestions.value = [];
  error.value = '';
  confirmed.value = false;
});

function search(index: number): void {
  activeRow.value = index;
  clearTimeout(timer);
  controller?.abort();
  suggestions.value = [];
  const query = drafts.value[index]?.trim() ?? '';
  if (query.length < 2) return;
  const current = new AbortController();
  controller = current;
  timer = setTimeout(async () => {
    try {
      const page = await getIngredients(query, 'all', 0, current.signal);
      if (!current.signal.aborted) suggestions.value = page.items;
    } catch { /* Manual text remains available when suggestions are offline. */ }
  }, 250);
}

async function save(): Promise<void> {
  if (saving.value || !changes.value.length || (props.uncertain && !confirmed.value)) return;
  saving.value = true;
  error.value = '';
  const corrections: IngredientCorrection[] = changes.value.map((row) => {
    const text = drafts.value[row.index]!.trim();
    const previous = ids.get(row.index);
    const id = previous?.text === text ? previous.id : crypto.randomUUID();
    ids.set(row.index, { text, id });
    return { id, tokenIndex: row.index, tokenRaw: row.raw, correctedText: text };
  });
  try {
    const result = await saveIngredientCorrections(props.requestId, props.composition, corrections, personalExclusions.value);
    if (!alive) return;
    drafts.value = {};
    ids.clear();
    editAll.value = false;
    emit('corrected', result);
  } catch (reason) {
    if (alive) error.value = reason instanceof Error ? reason.message : 'Не удалось сохранить исправления';
  } finally {
    if (alive) saving.value = false;
  }
}

onBeforeUnmount(() => { alive = false; clearTimeout(timer); controller?.abort(); });
</script>

<template>
  <section class="corrections" aria-labelledby="corrections-title">
    <header class="corrections__header">
      <h2 id="corrections-title">{{ uncertain ? 'Проверьте состав' : rows.length ? 'Требуют уточнения' : 'Уточнение состава' }} <span v-if="rows.length">{{ rows.length }}</span></h2>
      <button v-if="!uncertain" type="button" class="corrections__edit" :disabled="saving" :aria-pressed="editAll" @click="editAll = !editAll">
        <Pencil :size="16" aria-hidden="true" />{{ editAll ? 'Только нераспознанные' : 'Исправить весь состав' }}
      </button>
    </header>
    <form v-if="rows.length" @submit.prevent="save">
      <fieldset :disabled="saving">
        <div v-for="row in rows" :key="row.index" class="correction-row">
          <p class="correction-row__raw">{{ row.raw || 'Состав не распознан' }}</p>
          <label :for="`${listId}-${row.index}`">Правильное название</label>
          <input :id="`${listId}-${row.index}`" v-model="drafts[row.index]" :list="activeRow === row.index ? listId : undefined"
            maxlength="500" placeholder="Например, E202 или сорбат калия" autocomplete="off"
            @input="search(row.index)" @focus="search(row.index)">
        </div>
        <datalist :id="listId">
          <option v-for="item in suggestions" :key="item.id" :value="[item.code, item.name].filter(Boolean).join(' ')"></option>
        </datalist>
      </fieldset>
      <p v-if="error" class="corrections__error" role="alert">{{ error }}</p>
      <label v-if="uncertain" class="corrections__confirmation"><input v-model="confirmed" type="checkbox" :disabled="saving">Состав проверен по этикетке</label>
      <button class="corrections__save" type="submit" :disabled="saving || !changes.length || changes.length > 50 || (uncertain && !confirmed)">
        <Save :size="18" aria-hidden="true" />{{ saving ? 'Сохраняем...' : 'Сохранить и проверить' }}
      </button>
      <p v-if="changes.length > 50" class="corrections__error">За один раз можно сохранить до 50 исправлений.</p>
    </form>
    <p v-else class="corrections__empty">Нераспознанных фрагментов нет</p>
  </section>
</template>

<style scoped>
.corrections { padding-block: 16px; border-block: 1px solid #dfe6e3; }
.corrections__header { display: flex; flex-wrap: wrap; gap: 12px; justify-content: space-between; align-items: center; margin-bottom: 12px; }
h2 { font-size: 16px; color: #17201d; }
h2 span { color: #747d80; margin-left: 6px; font-size: 13px; }
.corrections__edit { display: inline-flex; align-items: center; gap: 6px; padding: 6px 0; border: 0; background: transparent; color: #176c56; font: inherit; font-size: 12px; cursor: pointer; }
fieldset { min-width: 0; border: 0; padding: 0; display: grid; gap: 12px; }
.correction-row { padding: 12px; border: 1px solid #d8dfe2; border-radius: 8px; background: white; min-width: 0; }
.correction-row__raw { color: #7b4934; font-size: 14px; line-height: 1.5; overflow-wrap: anywhere; margin-bottom: 10px; }
label { display: block; font-size: 12px; color: #5c6863; margin-bottom: 5px; }
input { width: 100%; min-width: 0; min-height: 44px; padding: 8px 10px; border: 1px solid #bfcac6; border-radius: 6px; font: inherit; font-size: 14px; }
input:focus-visible { outline: 2px solid #218060; outline-offset: 2px; }
.corrections__save { display: inline-flex; gap: 8px; align-items: center; justify-content: center; width: 100%; min-height: 44px; margin-top: 12px; border: 0; border-radius: 8px; background: #19845f; color: white; padding: 10px; font: inherit; font-size: 14px; cursor: pointer; }
button:disabled { opacity: .55; cursor: default; }
.corrections__error { color: #b42332; font-size: 13px; margin-top: 10px; overflow-wrap: anywhere; }
.corrections__empty { font-size: 13px; color: #66736e; }
.corrections__confirmation { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
.corrections__confirmation input { width: 20px; height: 20px; min-height: 20px; flex: 0 0 auto; }
</style>
