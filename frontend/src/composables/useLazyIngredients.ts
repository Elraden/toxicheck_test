import { computed, nextTick, onBeforeUnmount, ref, watch, type Ref } from 'vue';
import { getIngredients, type IngredientSummary } from '@/services/backendApi';

export function useLazyIngredients(query: Ref<string>, kind: Ref<string>, status: Ref<string>) {
  const items = ref<IngredientSummary[]>([]);
  const total = ref<number | null>(null);
  const loading = ref(false);
  const error = ref('');
  const sentinel = ref<HTMLElement | null>(null);
  const offset = ref(0);
  const hasMore = computed(() => total.value === null || offset.value < total.value);
  let controller: AbortController | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let observer: IntersectionObserver | undefined;
  let disposed = false;

  function nearEnd() {
    const rect = sentinel.value?.getBoundingClientRect();
    return !!rect && rect.top < window.innerHeight + 240 && rect.bottom >= 0;
  }

  async function loadMore(): Promise<void> {
    if (disposed || loading.value || !hasMore.value) return;
    const current = new AbortController();
    controller = current;
    loading.value = true;
    error.value = '';
    try {
      const page = await getIngredients(query.value.trim(), kind.value, offset.value, current.signal, status.value);
      if (current.signal.aborted) return;
      const seen = new Set(items.value.map(item => item.id));
      items.value.push(...page.items.filter(item => !seen.has(item.id)));
      offset.value = page.offset + page.items.length;
      total.value = page.items.length ? page.total : offset.value;
    } catch (reason) {
      if (!current.signal.aborted) error.value = reason instanceof Error ? reason.message : 'Не удалось загрузить ингредиенты';
    } finally {
      if (!current.signal.aborted) {
        loading.value = false;
        await nextTick();
        if (!error.value && hasMore.value && nearEnd()) void loadMore();
      }
    }
  }

  function reset() {
    clearTimeout(timer);
    controller?.abort();
    items.value = [];
    total.value = null;
    offset.value = 0;
    error.value = '';
    loading.value = true;
    timer = setTimeout(() => { loading.value = false; void loadMore(); }, 250);
  }

  watch([query, kind, status], reset, { immediate: true });
  watch(sentinel, element => {
    observer?.disconnect();
    if (!element || !('IntersectionObserver' in window)) return;
    observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting) && !error.value) void loadMore();
    }, { rootMargin: '240px' });
    observer.observe(element);
  }, { flush: 'post' });
  onBeforeUnmount(() => { disposed = true; clearTimeout(timer); controller?.abort(); observer?.disconnect(); });
  return { items, total, loading, error, sentinel, hasMore, loadMore };
}
