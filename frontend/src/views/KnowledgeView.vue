<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ArrowLeft, BookOpen, ChevronLeft, ChevronRight, Search, X } from 'lucide-vue-next';
import IngredientSafetyBadge from '@/components/IngredientSafetyBadge.vue';
import { getIngredients, type IngredientPage } from '@/services/backendApi';

const route = useRoute();
const router = useRouter();
const query = ref('');
const kind = ref('all');
const status = ref('all');
const page = ref<IngredientPage | null>(null);
const loading = ref(false);
const error = ref('');
let controller: AbortController | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;

function readOffset() {
  const value = Number(route.query.offset || 0);
  return Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

async function load() {
  controller?.abort();
  const request = new AbortController();
  controller = request;
  loading.value = true;
  error.value = '';
  page.value = null;
  try {
    const result = await getIngredients(query.value, kind.value, readOffset(), request.signal, status.value);
    if (!request.signal.aborted) page.value = result;
  } catch (cause) {
    if (!request.signal.aborted) error.value = cause instanceof Error ? cause.message : 'Не удалось загрузить ингредиенты';
  } finally {
    if (!request.signal.aborted) loading.value = false;
  }
}

function navigate(offset = 0) {
  clearTimeout(timer);
  const target = { name: 'knowledge', query: {
    ...(query.value.trim() ? { q: query.value.trim() } : {}),
    ...(kind.value !== 'all' ? { kind: kind.value } : {}),
    ...(status.value !== 'all' ? { status: status.value } : {}),
    ...(offset ? { offset: String(offset) } : {})
  } };
  if (router.resolve(target).fullPath === route.fullPath) void load();
  else void router.replace(target);
}

function search() {
  clearTimeout(timer);
  controller?.abort();
  page.value = null;
  loading.value = true;
  timer = setTimeout(() => {
    if (query.value.trim() === (route.query.q || '') && readOffset() === 0) void load();
    else navigate();
  }, 300);
}

watch(() => route.query, () => {
  clearTimeout(timer);
  query.value = typeof route.query.q === 'string' ? route.query.q.slice(0, 200) : '';
  kind.value = ['additives', 'foods'].includes(String(route.query.kind)) ? String(route.query.kind) : 'all';
  status.value = ['neutral', 'attention', 'restricted', 'unknown'].includes(String(route.query.status)) ? String(route.query.status) : 'all';
  void load();
}, { immediate: true });

onBeforeUnmount(() => { clearTimeout(timer); controller?.abort(); });
</script>

<template>
  <section class="knowledge-screen">
    <header class="knowledge-header">
      <RouterLink :to="{ name: 'home' }" class="icon-button" aria-label="На главную" title="На главную"><ArrowLeft /></RouterLink>
      <h1>База знаний</h1>
      <BookOpen class="header-symbol" aria-hidden="true" />
    </header>
    <div class="knowledge-content">
      <form class="search-field" role="search" @submit.prevent="navigate()">
        <Search aria-hidden="true" />
        <input v-model="query" type="search" maxlength="200" placeholder="Название или E-код" aria-label="Поиск ингредиентов" @input="search">
        <button v-if="query" class="icon-button" type="button" aria-label="Очистить поиск" title="Очистить поиск" @click="query = ''; search()"><X /></button>
      </form>
      <div class="catalog-filters">
        <div class="catalog-filter">
        <label for="ingredient-kind">Ингредиенты</label>
        <select id="ingredient-kind" v-model="kind" @change="navigate()">
          <option value="all">Все</option>
          <option value="additives">Е-добавки</option>
          <option value="foods">Без E-кода</option>
        </select>
        </div>
        <div class="catalog-filter">
          <label for="ingredient-status">Статус</label>
          <select id="ingredient-status" v-model="status" @change="navigate()">
            <option value="all">Все статусы</option>
            <option value="neutral">Нет предупреждений</option>
            <option value="attention">Внимание</option>
            <option value="restricted">Ограничения</option>
            <option value="unknown">Оценки нет</option>
          </select>
        </div>
      </div>
      <p v-if="loading" class="catalog-state" role="status">Загружаем ингредиенты...</p>
      <div v-else-if="error" class="catalog-state catalog-error" role="alert">
        <p>{{ error }}</p><button class="text-button" @click="load">Повторить</button>
      </div>
      <template v-else-if="page">
        <p class="catalog-count" role="status">Найдено: {{ page.total }}</p>
        <p v-if="!page.items.length" class="catalog-state">Ингредиенты не найдены</p>
        <ul class="catalog-list">
          <li v-for="item in page.items" :key="item.id">
            <RouterLink :to="{ name: 'ingredient', params: { id: item.id } }" class="catalog-item">
              <div class="catalog-item__body">
                <span v-if="item.code" class="catalog-code">{{ item.code }}</span>
                <h2>{{ item.name }}</h2>
                <p v-if="item.category" class="catalog-category">{{ item.category }}</p>
                <IngredientSafetyBadge :severity="item.severity" />
              </div>
              <ChevronRight aria-hidden="true" />
            </RouterLink>
          </li>
        </ul>
        <nav v-if="page.total > page.limit || page.offset" class="pagination" aria-label="Страницы каталога">
          <button class="icon-button" :disabled="!page.offset" aria-label="Предыдущая страница" title="Предыдущая страница" @click="navigate(Math.max(0, page.offset - page.limit))"><ChevronLeft /></button>
          <span>{{ Math.floor(page.offset / page.limit) + 1 }} / {{ Math.max(1, Math.ceil(page.total / page.limit)) }}</span>
          <button class="icon-button" :disabled="page.offset + page.limit >= page.total" aria-label="Следующая страница" title="Следующая страница" @click="navigate(page.offset + page.limit)"><ChevronRight /></button>
        </nav>
      </template>
    </div>
  </section>
</template>

<style scoped>
.knowledge-screen { color: #17201d; min-height: calc(100dvh - 72px); }
.knowledge-header { display: grid; grid-template-columns: 40px minmax(0, 1fr) 24px; align-items: center; gap: 8px; padding: 8px 16px; border-bottom: 1px solid #dfe6e3; background: #fff; }
h1 { font-size: 20px; line-height: 1.3; overflow-wrap: anywhere; }
.header-symbol { color: #687780; width: 22px; }
.icon-button { display: grid; place-items: center; flex-shrink: 0; width: 40px; height: 40px; border: 0; border-radius: 8px; background: transparent; color: inherit; cursor: pointer; }
.icon-button svg { width: 22px; height: 22px; }
.icon-button:disabled { opacity: .35; cursor: default; }
.knowledge-content { padding: 20px 16px 24px; }
.search-field { display: flex; align-items: center; gap: 8px; height: 48px; padding: 0 4px 0 12px; background: #fff; border: 1px solid #cdd8d3; border-radius: 8px; }
.search-field > svg { width: 20px; flex-shrink: 0; color: #687780; }
.search-field input { width: 100%; min-width: 0; border: 0; background: transparent; color: inherit; font: inherit; font-size: 14px; }
.search-field input:focus { outline: none; }
.search-field:focus-within { outline: 2px solid #25a777; outline-offset: 2px; }
.search-field input::-webkit-search-cancel-button { display: none; }
.catalog-filters { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr); gap: 12px; margin: 16px 0; font-size: 13px; }
.catalog-filter { display: grid; gap: 6px; min-width: 0; }
.catalog-filters select { width: 100%; min-width: 0; height: 42px; padding: 8px; font: inherit; color: inherit; border: 1px solid #cdd8d3; border-radius: 6px; background: #fff; }
@media (max-width: 380px) { .catalog-filters { grid-template-columns: minmax(0, 1fr); } }
.catalog-count, .catalog-category { color: #66736e; font-size: 12px; line-height: 1.5; }
.catalog-count { margin-bottom: 12px; }
.catalog-list { list-style: none; display: grid; gap: 10px; }
.catalog-item { display: grid; grid-template-columns: minmax(0, 1fr) 20px; align-items: center; gap: 12px; padding: 14px; border: 1px solid #dfe6e3; border-radius: 8px; background: #fff; color: inherit; text-decoration: none; }
.catalog-item:hover { border-color: #8cb8a5; }
.catalog-item__body { display: grid; justify-items: start; gap: 7px; min-width: 0; overflow-wrap: anywhere; }
.catalog-item > svg { width: 20px; color: #87948e; }
.catalog-item h2 { font-size: 15px; line-height: 1.45; }
.catalog-code { font-size: 13px; color: #21664e; font-weight: 700; }
.catalog-state { padding: 40px 8px; text-align: center; color: #66736e; font-size: 14px; line-height: 1.6; }
.catalog-error { color: #a42e38; }
.text-button { margin-top: 12px; padding: 10px; border: 0; background: transparent; color: #167759; font: inherit; cursor: pointer; }
.pagination { display: flex; justify-content: center; align-items: center; gap: 20px; padding-top: 20px; font-size: 13px; }
:is(button, a, select):focus-visible { outline: 2px solid #25a777; outline-offset: 3px; }
</style>
