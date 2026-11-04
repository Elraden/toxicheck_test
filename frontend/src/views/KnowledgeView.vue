<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ArrowLeft, BookOpen, ChevronRight, Search, X } from 'lucide-vue-next';
import IngredientSafetyBadge from '@/components/IngredientSafetyBadge.vue';
import { useLazyIngredients } from '@/composables/useLazyIngredients';

const route = useRoute();
const router = useRouter();
const query = ref('');
const kind = ref('all');
const status = ref('all');
const { items, total, loading, error, sentinel, hasMore, loadMore } = useLazyIngredients(query, kind, status);
let timer: ReturnType<typeof setTimeout> | undefined;

function navigate() {
  clearTimeout(timer);
  const target = { name: 'knowledge', query: {
    ...(query.value.trim() ? { q: query.value.trim() } : {}),
    ...(kind.value !== 'all' ? { kind: kind.value } : {}),
    ...(status.value !== 'all' ? { status: status.value } : {})
  } };
  if (router.resolve(target).fullPath !== route.fullPath) void router.replace(target);
}

function search() {
  clearTimeout(timer);
  timer = setTimeout(navigate, 300);
}

watch(() => route.query, () => {
  clearTimeout(timer);
  query.value = typeof route.query.q === 'string' ? route.query.q.slice(0, 200) : '';
  kind.value = ['additives', 'foods'].includes(String(route.query.kind)) ? String(route.query.kind) : 'all';
  status.value = ['neutral', 'attention', 'restricted', 'unknown'].includes(String(route.query.status)) ? String(route.query.status) : 'all';
}, { immediate: true });

onBeforeUnmount(() => { clearTimeout(timer); });
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
        <p v-if="total !== null" class="catalog-count" role="status">Найдено: {{ total }}</p>
        <p v-if="!loading && !error && total === 0" class="catalog-state">Ингредиенты не найдены</p>
        <ul class="catalog-list">
          <li v-for="item in items" :key="item.id">
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
        <div ref="sentinel" class="catalog-state">
          <p v-if="loading" role="status">Загружаем ингредиенты...</p>
          <div v-else-if="error" class="catalog-error" role="alert"><p>{{ error }}</p><button class="text-button" @click="loadMore">Повторить</button></div>
          <button v-else-if="hasMore" class="text-button" @click="loadMore">Загрузить ещё</button>
          <span v-else-if="items.length">Все ингредиенты загружены</span>
        </div>
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
:is(button, a, select):focus-visible { outline: 2px solid #25a777; outline-offset: 3px; }
</style>
