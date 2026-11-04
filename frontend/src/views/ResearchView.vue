<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { request } from '@/services/http';
import { loadPersonalPreferences } from '@/services/personalExclusions';
import { researchWrite, stageNames, type Study, type RunSummary, type ResearchRun } from '@/services/research';
import '@/research.css';

const route = useRoute(), router = useRouter();
const studies = ref<Study[]>([]), runs = ref<RunSummary[]>([]);
const selected = ref(''), title = ref(''), consent = ref(false), busy = ref(false), error = ref('');
const study = computed(() => studies.value.find(item => item.id === selected.value));
let creationId = crypto.randomUUID();
async function load() {
  busy.value = true; error.value = '';
  try {
    await loadPersonalPreferences();
    [studies.value, runs.value] = await Promise.all([request<Study[]>('/research/studies'), request<RunSummary[]>('/research/runs')]);
    if (route.query.study) selected.value = String(route.query.study);
  } catch (e) { error.value = e instanceof Error ? e.message : 'Не удалось загрузить исследования'; }
  finally { busy.value = false; }
}
async function start() {
  if (!study.value || !consent.value || busy.value) return;
  busy.value = true; error.value = '';
  try {
    const run = await researchWrite<ResearchRun>('/runs', { id: creationId, study_id: selected.value, title: title.value.trim(), consent: true });
    creationId = crypto.randomUUID();
    await router.push({ name: 'research-run', params: { runId: run.id } });
  } catch (e) { error.value = e instanceof Error ? e.message : 'Не удалось начать'; }
  finally { busy.value = false; }
}
onMounted(load);
</script>
<template>
  <main class="research-shell">
    <header class="research-heading"><RouterLink to="/app/home">← На главную</RouterLink><span class="research-tag">ToxiCheck · исследования</span></header>
    <h1>Помогите проверить ToxiCheck</h1>
    <p>Выберите исследование. Ответы, время проверки и фотографии помогут команде оценить качество приложения.</p>
    <p class="research-note">Один браузер — один участник. Используйте своё устройство и не очищайте данные браузера до завершения. На общем устройстве результаты разных людей могут объединиться.</p>
    <p v-if="error" class="research-error" role="alert">{{ error }} <button @click="load">Повторить загрузку</button></p>
    <p v-if="busy" role="status">Загружаем…</p>
    <div class="research-grid">
      <button v-for="item in studies" :key="item.id" class="research-card research-choice" :class="{ selected: selected === item.id }" :aria-pressed="selected === item.id" @click="selected = item.id">
        <span class="research-tag">{{ item.scenario === 'comparison' ? 'Ручная проверка → ToxiCheck → Разметка' : 'OCR → Разметка' }}</span>
        <strong>{{ item.title }}</strong><span>Протокол {{ item.protocol_version }}</span>
      </button>
    </div>
    <p v-if="!busy && !error && !studies.length">Сейчас нет открытых исследований.</p>
    <form v-if="study" class="research-card research-form" @submit.prevent="start">
      <h2>{{ study.title }}</h2><p class="research-prewrap">{{ study.instruction }}</p>
      <p v-if="study.scenario === 'comparison'">Для этого сценария заранее выберите E-добавки в <RouterLink to="/app/preferences">предпочтениях</RouterLink>. Не сканируйте этот продукт до ручного этапа.</p>
      <label>Название продукта для этой проверки<input v-model="title" maxlength="200" required placeholder="Например, печенье — упаковка 150 г"></label>
      <label class="research-check"><input v-model="consent" type="checkbox" required>Я согласен на сохранение моих ответов, времени, результатов анализа и фотографий этикетки для исследования и их проверку командой. На фото не должно быть лиц, чеков или других личных данных.</label>
      <button class="research-primary" :disabled="busy || !consent">Перейти к заданию</button>
    </form>
    <section v-if="runs.length" class="research-card"><h2>Мои проверки</h2>
      <RouterLink v-for="run in runs" :key="run.id" class="research-list-row" :to="{ name: 'research-run', params: { runId: run.id } }">
        <span><strong>{{ run.title }}</strong><small>{{ run.study_title }}</small></span><span>{{ stageNames[run.stage] }}</span>
      </RouterLink>
    </section>
  </main>
</template>
