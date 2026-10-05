<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ArrowLeft, Heart, FileText, FlaskConical, Leaf, Info, ShieldQuestion } from 'lucide-vue-next';
import IngredientSafetyBadge from '@/components/IngredientSafetyBadge.vue';
import IngredientRules from '@/components/IngredientRules.vue';
import { getIngredient, type IngredientDetail } from '@/services/backendApi';

const route = useRoute();
const router = useRouter();
const ingredient = ref<IngredientDetail | null>(null);
const loading = ref(false);
const error = ref('');
const favoriteError = ref('');
const favoriteKey = 'toxicheck.favorite-ingredients';
const favorites = ref<string[]>(readFavorites());
let controller: AbortController | undefined;
const favorite = computed(() => ingredient.value ? favorites.value.includes(ingredient.value.id) : false);
const description = computed(() => ingredient.value?.full_description?.trim() || ingredient.value?.description?.trim());
const functions = computed(() => ingredient.value?.functions.filter(value => value.trim()).join(', ') || ingredient.value?.category?.trim());
const origins = computed(() => ingredient.value?.origins.filter(value => value.trim()).join(', '));
const assessmentText = computed(() => {
  if (!ingredient.value || ingredient.value.severity === 'unknown') {
    return 'Недостаточно данных для оценки.';
  }
  if (ingredient.value.severity === 'neutral') {
    return 'Учитывайте условия применения и личные ограничения.';
  }
  return 'Есть ограничения или предупреждения.';
});

function readFavorites(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(favoriteKey) || '[]');
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [];
  } catch { return []; }
}

function toggleFavorite() {
  if (!ingredient.value) return;
  const id = ingredient.value.id;
  const stored = readFavorites();
  const next = stored.includes(id) ? stored.filter((value) => value !== id) : [...stored, id];
  try {
    localStorage.setItem(favoriteKey, JSON.stringify(next));
    favorites.value = next;
    favoriteError.value = '';
  } catch { favoriteError.value = 'Не удалось сохранить избранное в браузере'; }
}

function goBack() {
  if (typeof router.options.history.state.back === 'string') router.back();
  else void router.replace({ name: 'knowledge' });
}

async function load() {
  controller?.abort();
  const request = new AbortController();
  controller = request;
  loading.value = true;
  error.value = '';
  ingredient.value = null;
  try {
    const result = await getIngredient(String(route.params.id), request.signal);
    if (!request.signal.aborted) ingredient.value = result;
  } catch (cause) {
    if (!request.signal.aborted) error.value = cause instanceof Error ? cause.message : 'Не удалось загрузить ингредиент';
  } finally {
    if (!request.signal.aborted) loading.value = false;
  }
}

watch(() => route.params.id, () => { favoriteError.value = ''; void load(); }, { immediate: true });
onBeforeUnmount(() => controller?.abort());
</script>

<template>
  <section class="ingredient-screen">
    <header class="detail-header">
      <button class="icon-button" aria-label="Назад" title="Назад" @click="goBack"><ArrowLeft /></button>
      <h1>Ингредиент</h1>
      <button class="icon-button favorite-button" :disabled="!ingredient" :aria-pressed="favorite" :aria-label="favorite ? 'Убрать из избранного' : 'В избранное'" :title="favorite ? 'Убрать из избранного' : 'В избранное'" @click="toggleFavorite"><Heart :fill="favorite ? 'currentColor' : 'none'" /></button>
    </header>
    <p v-if="loading" class="detail-state" role="status">Загружаем карточку...</p>
    <div v-else-if="error" class="detail-state" role="alert"><p>{{ error }}</p><button class="retry-button" @click="load">Повторить</button></div>
    <template v-else-if="ingredient">
      <div class="ingredient-identity">
        <span v-if="ingredient.code" class="ingredient-code">{{ ingredient.code }}</span>
        <h2>{{ ingredient.name }}</h2>
        <p v-if="ingredient.name_en">{{ ingredient.name_en }}</p>
      </div>
      <p v-if="favoriteError" class="favorite-error" role="alert">{{ favoriteError }}</p>
      <div class="detail-content">
        <section class="detail-section assessment-section">
          <ShieldQuestion class="section-icon section-icon--assessment" aria-hidden="true" />
          <div><h3>Оценка</h3><IngredientSafetyBadge :severity="ingredient.severity" /><p>{{ assessmentText }}</p></div>
        </section>
        <section class="detail-section">
          <FileText class="section-icon section-icon--description" aria-hidden="true" />
          <div>
            <h3>Описание</h3>
            <p class="description-text">{{ description || 'Описание пока не добавлено' }}</p>
          </div>
        </section>
        <section v-if="functions" class="detail-section">
          <FlaskConical class="section-icon section-icon--function" aria-hidden="true" />
          <div><h3>Функция</h3><p>{{ functions }}</p></div>
        </section>
        <section v-if="origins" class="detail-section">
          <Leaf class="section-icon section-icon--origin" aria-hidden="true" />
          <div><h3>Происхождение</h3><p>{{ origins }}</p></div>
        </section>
        <section v-if="ingredient.aliases.length" class="detail-section">
          <Info class="section-icon" aria-hidden="true" />
          <div><details><summary>Другие названия ({{ ingredient.aliases.length }})</summary><ul class="alias-list"><li v-for="alias in ingredient.aliases" :key="alias">{{ alias }}</li></ul></details></div>
        </section>
        <section v-if="ingredient.rules.length" class="rule-section">
          <h3>Условия применения</h3>
          <IngredientRules :rules="ingredient.rules" />
        </section>
      </div>
    </template>
  </section>
</template>

<style scoped>
.ingredient-screen { color: #17201d; min-height: calc(100dvh - 72px); }
.detail-header { display: grid; grid-template-columns: 40px minmax(0, 1fr) 40px; align-items: center; gap: 8px; padding: 8px 16px; background: #fff; border-bottom: 1px solid #dfe6e3; }
.detail-header h1 { font-size: 18px; text-align: center; overflow-wrap: anywhere; }
.icon-button { display: grid; place-items: center; width: 40px; height: 40px; border: 0; border-radius: 8px; background: transparent; color: inherit; cursor: pointer; }
.icon-button svg { width: 24px; height: 24px; }
.icon-button:disabled { opacity: .35; cursor: default; }
.favorite-button[aria-pressed="true"] { color: #b74662; }
.ingredient-identity { padding: 28px 24px; background: #edf6f1; border-bottom: 1px solid #dfe6e3; overflow-wrap: anywhere; }
.ingredient-code { display: inline-block; max-width: 100%; margin-bottom: 12px; padding: 6px 12px; border-radius: 8px; color: #155b43; background: #d9eee3; font-size: 30px; line-height: 1.2; font-weight: 700; }
.ingredient-identity h2 { font-size: 26px; line-height: 1.25; }
.ingredient-identity p { margin-top: 10px; color: #66736e; font-size: 13px; line-height: 1.5; }
.detail-content { padding: 0 20px 24px; }
.detail-section { display: grid; grid-template-columns: 40px minmax(0, 1fr); gap: 14px; padding: 22px 0; border-bottom: 1px solid #dfe6e3; }
.section-icon { width: 40px; height: 40px; padding: 9px; border-radius: 8px; background: #edf0f2; color: #53616a; }
.section-icon--description { background: #e8f0f6; color: #426781; }
.section-icon--function { background: #e2f2eb; color: #28644c; }
.section-icon--origin { background: #edf0df; color: #5b6833; }
.section-icon--assessment { background: #f6efdd; color: #83662b; }
.detail-content h3 { margin-bottom: 10px; font-size: 17px; line-height: 1.4; }
.detail-content p { font-size: 14px; line-height: 1.7; color: #52605a; margin-top: 8px; }
.detail-content { overflow-wrap: anywhere; }
.description-text { white-space: pre-line; }
.detail-content summary { padding: 8px 0; cursor: pointer; color: #24684f; font-size: 14px; line-height: 1.5; }
.alias-list { padding-left: 18px; font-size: 13px; line-height: 1.8; color: #52605a; }
.rule-section { padding-top: 22px; }
.detail-state { padding: 40px 24px; text-align: center; font-size: 14px; line-height: 1.6; }
.retry-button { margin-top: 12px; padding: 10px; border: 0; color: #167759; background: transparent; font: inherit; cursor: pointer; }
.favorite-error { padding: 12px 20px; color: #a42e38; font-size: 13px; }
:is(button, a, summary):focus-visible { outline: 2px solid #25a777; outline-offset: 3px; }
</style>
