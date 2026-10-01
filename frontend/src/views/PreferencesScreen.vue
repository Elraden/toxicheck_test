<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { Check, Search, ShieldAlert, X } from 'lucide-vue-next';
import { getPreferenceIngredients, type PreferenceIngredient } from '@/services/backendApi';
import { readPersonalExclusions, exclusionsStorageKey } from '@/services/personalExclusions';

const ingredientPreferences = ref<PreferenceIngredient[]>([]);
const selectedIds = ref<string[]>([]);
const searchQuery = ref('');
const loading = ref(true);
const error = ref('');
const storageError = ref('');
const visibleLimit = ref(60);
let controller: AbortController | undefined;
const selectedIdSet = computed(() => new Set(selectedIds.value));
const selectedIngredients = computed(() => selectedIds.value.map((id) =>
  ingredientPreferences.value.find((ingredient) => ingredient.id === id) ||
  { id, name: id, code: null, category: '', description: '', legacy_ids: [] }
));
const filteredIngredients = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase();
  return ingredientPreferences.value.filter((ingredient) =>
    !query || [ingredient.name, ingredient.code || '', ingredient.category, ingredient.description]
      .join(' ').toLocaleLowerCase().includes(query)
  );
});
const visibleIngredients = computed(() => filteredIngredients.value.slice(0, visibleLimit.value));
watch(searchQuery, () => { visibleLimit.value = 60; });

function persist(): void {
  try {
    window.localStorage.setItem(exclusionsStorageKey, JSON.stringify(selectedIds.value));
    storageError.value = '';
  } catch {
    storageError.value = 'Браузер не разрешил сохранить исключения. Они доступны только до закрытия страницы.';
  }
}

async function loadCatalog(): Promise<void> {
  controller?.abort();
  const current = new AbortController();
  controller = current;
  loading.value = true;
  error.value = '';
  try {
    const catalog = await getPreferenceIngredients(current.signal);
    if (current.signal.aborted) return;
    ingredientPreferences.value = catalog;
    const legacyIds = new Map(catalog.flatMap((item) =>
      item.legacy_ids.map((id) => [id, item.id] as const)));
    const migrated = [...new Set(selectedIds.value.map((id) => legacyIds.get(id) || id))];
    if (JSON.stringify(migrated) !== JSON.stringify(selectedIds.value)) {
      selectedIds.value = migrated;
      persist();
    }
  } catch (reason) {
    if (!current.signal.aborted) error.value = reason instanceof Error ? reason.message : 'Не удалось загрузить справочник';
  } finally {
    if (!current.signal.aborted) loading.value = false;
  }
}

function toggleIngredient(ingredient: PreferenceIngredient): void {
  if (selectedIdSet.value.has(ingredient.id)) removeIngredient(ingredient.id);
  else {
    selectedIds.value = [...selectedIds.value, ingredient.id];
    persist();
  }
}
function removeIngredient(id: string): void {
  selectedIds.value = selectedIds.value.filter((selected) => selected !== id);
  persist();
}
function clearSelected(): void {
  selectedIds.value = [];
  persist();
}
onMounted(() => {
  selectedIds.value = readPersonalExclusions();
  void loadCatalog();
});
onBeforeUnmount(() => controller?.abort());
</script>

<template>
  <main class="preferences">
    <header class="preferences__header">
      <div>
        <p class="preferences__eyebrow">
          Персональные исключения
        </p>

        <h1 class="preferences__title">
          Предпочтения
        </h1>
      </div>

      <div class="preferences__counter">
        <ShieldAlert aria-hidden="true" />

        <span>
          {{ selectedIngredients.length }}
        </span>
      </div>
    </header>

    <section class="selected-panel">
      <div class="selected-panel__header">
        <h2 class="selected-panel__title">
          Вредно для меня
        </h2>

        <button
          v-if="selectedIngredients.length"
          type="button"
          class="selected-panel__clear"
          @click="clearSelected"
        >
          Очистить
        </button>
      </div>

      <div
        v-if="selectedIngredients.length"
        class="selected-panel__chips"
      >
        <button
          v-for="ingredient in selectedIngredients"
          :key="ingredient.id"
          type="button"
          class="selected-chip"
          @click="removeIngredient(ingredient.id)"
        >
          <span>
            {{ ingredient.name }}
          </span>

          <X aria-hidden="true" />
        </button>
      </div>

      <p
        v-else
        class="selected-panel__empty"
      >
        Список исключений пока пуст.
      </p>
    </section>

    <p v-if="storageError" role="alert">{{ storageError }}</p>
    <p v-if="loading" role="status">Загружаем ингредиенты...</p>
    <div v-else-if="error" role="alert">
      <p>{{ error }}</p>
      <button type="button" class="selected-panel__clear" @click="loadCatalog">Повторить загрузку</button>
    </div>

    <label class="preferences-search">
      <Search aria-hidden="true" />

      <input
        v-model="searchQuery"
        type="search"
        placeholder="Найти ингредиент или E-код"
      >
    </label>

    <section class="ingredients-section">
      <div class="ingredients-section__header">
        <h2 class="ingredients-section__title">
          Ингредиенты
        </h2>

        <span>
          {{ filteredIngredients.length }}
        </span>
      </div>

      <div class="ingredients-list">
        <button
          v-for="ingredient in visibleIngredients"
          :key="ingredient.id"
          type="button"
          class="ingredient-option"
          role="checkbox"
          :aria-checked="selectedIdSet.has(ingredient.id)"
          :class="{
            'ingredient-option--selected':
              selectedIdSet.has(ingredient.id)
          }"
          @click="toggleIngredient(ingredient)"
        >
          <span class="ingredient-option__checkbox">
            <Check
              v-if="selectedIdSet.has(ingredient.id)"
              aria-hidden="true"
            />
          </span>

          <span class="ingredient-option__content">
            <span class="ingredient-option__name">
              {{ ingredient.name }}

              <span
                v-if="ingredient.code"
                class="ingredient-option__code"
              >
                {{ ingredient.code }}
              </span>
            </span>

            <span v-if="ingredient.category || ingredient.description" class="ingredient-option__meta">
              {{ [ingredient.category, ingredient.description].filter(Boolean).join(' · ') }}
            </span>
          </span>
        </button>
      </div>
      <p v-if="!loading && !error && !filteredIngredients.length">Ингредиенты не найдены</p>
      <button v-if="visibleLimit < filteredIngredients.length" type="button" class="selected-panel__clear" @click="visibleLimit += 60">Показать ещё</button>
    </section>
  </main>
</template>

<style scoped>
.preferences {
  min-height: calc(100dvh - 72px);
  padding: 24px;
  background-color: #f6f8f7;
}

.preferences__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.preferences__eyebrow {
  margin: 0 0 4px;
  color: #66736e;
  font-size: 13px;
}

.preferences__title {
  margin: 0;
  color: #17201d;
  font-size: 24px;
  line-height: 1.2;
}

.preferences__counter {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-width: 56px;
  height: 40px;
  padding: 0 12px;
  border: 1px solid #bce2d3;
  border-radius: 999px;
  color: #16875f;
  background-color: #e9f6f1;
  font-weight: 800;
}

.preferences__counter svg {
  width: 18px;
  height: 18px;
  stroke-width: 2.2;
}

.selected-panel {
  padding: 16px;
  border: 1px solid #dfe6e3;
  border-radius: 12px;
  background-color: #ffffff;
}

.selected-panel__header,
.ingredients-section__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.selected-panel__title,
.ingredients-section__title {
  margin: 0;
  color: #17201d;
  font-size: 15px;
  line-height: 1.3;
}

.selected-panel__clear {
  min-height: 32px;
  border: 0;
  color: #ef4444;
  background: transparent;
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.selected-panel__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.selected-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  min-height: 34px;
  padding: 7px 10px;
  border: 1px solid #facb7a;
  border-radius: 999px;
  color: #925b00;
  background-color: #fff8e8;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.selected-chip span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.selected-chip svg {
  flex: 0 0 auto;
  width: 14px;
  height: 14px;
  stroke-width: 2.4;
}

.selected-panel__empty {
  margin: 12px 0 0;
  color: #87938f;
  font-size: 13px;
  line-height: 1.5;
}

.preferences-search {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 46px;
  margin: 16px 0;
  padding: 0 14px;
  border: 1px solid #dfe6e3;
  border-radius: 12px;
  color: #87938f;
  background-color: #ffffff;
}

.preferences-search svg {
  flex: 0 0 auto;
  width: 18px;
  height: 18px;
  stroke-width: 2.2;
}

.preferences-search input {
  min-width: 0;
  width: 100%;
  border: 0;
  outline: 0;
  color: #17201d;
  background: transparent;
  font: inherit;
  font-size: 14px;
}

.preferences-search input::placeholder {
  color: #98a5a0;
}

.ingredients-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ingredients-section__header span {
  color: #87938f;
  font-size: 13px;
  font-weight: 700;
}

.ingredients-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.ingredient-option {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  gap: 12px;
  width: 100%;
  min-height: 64px;
  padding: 12px;
  border: 1px solid #dfe6e3;
  border-radius: 12px;
  color: inherit;
  background-color: #ffffff;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.ingredient-option--selected {
  border-color: #25a777;
  background-color: #effaf6;
}

.ingredient-option__checkbox {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  margin-top: 2px;
  border: 1px solid #cbd8d3;
  border-radius: 7px;
  color: #ffffff;
  background-color: #ffffff;
}

.ingredient-option--selected .ingredient-option__checkbox {
  border-color: #25a777;
  background-color: #25a777;
}

.ingredient-option__checkbox svg {
  width: 16px;
  height: 16px;
  stroke-width: 3;
}

.ingredient-option__content {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 5px;
}

.ingredient-option__name {
  color: #17201d;
  font-size: 14px;
  font-weight: 800;
  line-height: 1.35;
}

.ingredient-option__code {
  margin-left: 5px;
  color: #66736e;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.ingredient-option__meta {
  color: #66736e;
  font-size: 12px;
  line-height: 1.4;
}

.ingredient-option:active {
  transform: translateY(1px);
}

@media (max-width: 360px) {
  .preferences {
    padding-right: 16px;
    padding-left: 16px;
  }

  .preferences__header {
    align-items: flex-start;
  }
}
.ingredient-option__name, .ingredient-option__meta { overflow-wrap: anywhere; }
.selected-chip span { white-space: normal; overflow-wrap: anywhere; }
</style>
