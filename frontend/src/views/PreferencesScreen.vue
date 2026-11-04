<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { Search, ShieldAlert, X } from 'lucide-vue-next';
import ExclusionIngredientCard from '@/components/ExclusionIngredientCard.vue';
import { useLazyIngredients } from '@/composables/useLazyIngredients';
import { getSelectedPreferenceIngredients, type IngredientSummary } from '@/services/backendApi';
import { loadPersonalPreferences, personalExclusions, preferencesLoaded, preferencesError, preferencesNotice,
  preferencesSaving, savePersonalPreferences, setPersonalExclusions } from '@/services/personalExclusions';
import { browserSession } from '@/services/userSession';

const selectedIds = personalExclusions;
const selectedNames = ref<Record<string, { name: string; code: string | null }>>({});
const searchQuery = ref('');
const kind = ref('all');
const status = ref('all');
const { items, total, loading, error, sentinel, hasMore, loadMore } = useLazyIngredients(searchQuery, kind, status);
const selectedError = ref('');
const selectedLoading = ref(false);
let selectionController: AbortController | undefined;
const selectedIdSet = computed(() => new Set(selectedIds.value));
const selectedIngredients = computed(() => selectedIds.value.map(id => ({ id,
  ...(selectedNames.value[id] || { name: selectedLoading.value ? 'Загружаем название...' : 'Ингредиент недоступен', code: null })
})));

async function loadSelected() {
  selectionController?.abort();
  const current = new AbortController();
  selectionController = current;
  selectedError.value = '';
  selectedLoading.value = true;
  try {
    await loadPersonalPreferences();
    const selected = await getSelectedPreferenceIngredients(selectedIds.value, current.signal);
    if (current.signal.aborted) return;
    selected.forEach(item => { selectedNames.value[item.id] = item; });
  } catch {
    if (!current.signal.aborted) selectedError.value = 'Не удалось загрузить названия ваших исключений';
  } finally { if (!current.signal.aborted) selectedLoading.value = false; }
}
function toggleIngredient(item: IngredientSummary) {
  selectedNames.value[item.id] = item;
  if (selectedIdSet.value.has(item.id)) removeIngredient(item.id);
  else setPersonalExclusions([...selectedIds.value, item.id]);
}
function removeIngredient(id: string) {
  setPersonalExclusions(selectedIds.value.filter(value => value !== id));
}
function clearSelected() { setPersonalExclusions([]); }
async function reloadPreferences() {
  try { await loadPersonalPreferences(true); await loadSelected(); } catch { /* The profile error is displayed below. */ }
}
onMounted(() => { void loadSelected(); });
onBeforeUnmount(() => selectionController?.abort());
</script>

<template>
  <main class="preferences">
    <header class="preferences__header">
      <div><p class="preferences__eyebrow">Персональные исключения</p><h1>Предпочтения</h1></div>
      <span class="preferences__counter" aria-label="Количество исключений"><ShieldAlert :size="18" aria-hidden="true" />{{ selectedIds.length }}</span>
    </header>
    <section class="selected-panel">
      <header class="selected-panel__header"><h2>Исключаю</h2><button v-if="selectedIds.length" class="text-button" :disabled="!preferencesLoaded" @click="clearSelected">Очистить</button></header>
      <div v-if="selectedIngredients.length" class="selected-panel__chips">
        <button v-for="item in selectedIngredients" :key="item.id" class="selected-chip" :disabled="!preferencesLoaded" :aria-label="`Убрать исключение: ${item.name}`" @click="removeIngredient(item.id)">
          <span>{{ [item.code, item.name].filter(Boolean).join(' ') }}</span><X :size="14" aria-hidden="true" />
        </button>
      </div>
      <p v-else class="muted">Список исключений пока пуст.</p>
      <div v-if="selectedError" role="alert"><p>{{ selectedError }}</p><button class="text-button" @click="loadSelected">Повторить</button></div>
      <p v-if="preferencesSaving" class="muted" role="status">Сохраняем исключения...</p>
      <p v-else-if="preferencesLoaded && !preferencesError" class="muted" role="status">Сохранено в профиле</p>
      <p v-if="preferencesNotice" class="muted">{{ preferencesNotice }}</p>
      <div v-if="preferencesError" role="alert">
        <p>{{ preferencesError }}</p>
        <button v-if="preferencesLoaded" class="text-button" @click="savePersonalPreferences">Повторить сохранение</button>
        <button class="text-button" :disabled="preferencesSaving" @click="reloadPreferences">Загрузить сохранённые настройки</button>
      </div>
    </section>
    <div class="profile-session">
      <span class="muted">Гостевой профиль</span>
      <RouterLink :to="{ name: 'admin' }">{{ browserSession?.admin ? 'Админ-режим' : 'Вход администратора' }}</RouterLink>
    </div>
    <label class="preferences-search"><Search :size="20" aria-hidden="true" /><input v-model="searchQuery" type="search" maxlength="200" aria-label="Найти ингредиент или E-код" placeholder="Найти ингредиент или E-код"></label>
    <section class="ingredients-section" aria-labelledby="ingredients-title">
      <header class="ingredients-section__header"><h2 id="ingredients-title">Ингредиенты</h2><span v-if="total !== null" class="muted">{{ total }}</span></header>
      <div class="ingredients-list">
        <ExclusionIngredientCard v-for="item in items" :key="item.id" :ingredient="item" :selected="selectedIdSet.has(item.id)" :disabled="!preferencesLoaded" @toggle="toggleIngredient(item)" />
      </div>
      <p v-if="!loading && !error && total === 0" class="list-state">Ингредиенты не найдены</p>
      <div ref="sentinel" class="list-state">
        <p v-if="loading" role="status">Загружаем ингредиенты...</p>
        <div v-else-if="error" role="alert"><p>{{ error }}</p><button class="text-button" @click="loadMore">Повторить</button></div>
        <button v-else-if="hasMore" class="text-button" @click="loadMore">Загрузить ещё</button>
        <span v-else-if="items.length" class="muted">Все ингредиенты загружены</span>
      </div>
    </section>
  </main>
</template>

<style scoped>
.preferences { min-height: calc(100dvh - 72px); padding: 24px; background: #f6f8f7; color: #17201d; }
.preferences__header, .selected-panel__header, .ingredients-section__header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
h1 { font-size: 24px; line-height: 1.3; overflow-wrap: anywhere; }
h2 { font-size: 16px; }
.preferences__eyebrow, .muted { color: #66736e; font-size: 12px; line-height: 1.5; }
.preferences__counter { display: flex; gap: 6px; align-items: center; border: 1px solid #e4b4bc; border-radius: 8px; color: #a32c3d; padding: 10px; font-size: 14px; }
.selected-panel { border-block: 1px solid #dfe6e3; padding: 16px 0; margin: 20px 0; }
.selected-panel .muted { margin-top: 12px; }
.selected-panel__chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.selected-chip { display: flex; align-items: center; gap: 6px; padding: 8px 10px; max-width: 100%; border: 1px solid #edb4bc; border-radius: 8px; background: #fff0f2; color: #a32c3d; font: inherit; font-size: 12px; cursor: pointer; }
.selected-chip span { overflow-wrap: anywhere; min-width: 0; }
.selected-chip svg { flex-shrink: 0; }
.text-button { border: 0; background: transparent; color: #196e54; font: inherit; font-size: 13px; padding: 10px 0; cursor: pointer; }
.selected-panel__header .text-button { color: #b42332; }
.preferences-search { display: flex; align-items: center; gap: 8px; border: 1px solid #cdd8d3; border-radius: 8px; background: white; padding: 0 12px; height: 48px; margin-bottom: 20px; color: #66736e; }
.preferences-search input { border: 0; background: transparent; outline: 0; font: inherit; font-size: 14px; min-width: 0; width: 100%; }
.preferences-search svg { flex-shrink: 0; }
.preferences-search:focus-within { outline: 2px solid #218060; outline-offset: 2px; }
.ingredients-list { display: grid; gap: 12px; margin-top: 12px; }
.list-state { min-height: 60px; padding: 18px 0; text-align: center; font-size: 13px; }
[role="alert"] { color: #b42332; font-size: 13px; overflow-wrap: anywhere; }
button:focus-visible { outline: 2px solid #218060; outline-offset: 3px; }
.profile-session { display: flex; flex-wrap: wrap; gap: 12px; justify-content: space-between; padding-block: 20px; border-top: 1px solid #dfe6e3; }
.profile-session a { color: #196e54; font-size: 13px; }
button:disabled { opacity: .5; cursor: default; }
@media (max-width: 360px) { .preferences { padding: 20px 16px; } }
</style>
