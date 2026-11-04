<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { request } from '@/services/http';
import { ensureGuestSession } from '@/services/userSession';
import { getIngredient, type IngredientDetail } from '@/services/backendApi';
import { rulePresentation } from '@/services/ingredientPresentation';
import ResearchAnswers from '@/components/ResearchAnswers.vue';
import { researchWrite, photoUrl, fileData, duration, stageNames, attemptNames, type Answers, type Attempt, type ResearchRun } from '@/services/research';
import '@/research.css';

const route = useRoute();
const runId = computed(() => String(route.params.runId));
const run = ref<ResearchRun | null>(null), answers = ref<Answers>({}), external = ref('');
const busy = ref(false), saving = ref(false), error = ref(''), notice = ref('');
const label = ref(''), skip = ref(false), selectedAttempt = ref('');
const photo = ref(''), selectedFile = ref(''), fileInput = ref<HTMLInputElement | null>(null);
const photoId = ref<string | null>(null), attemptId = ref<string | null>(null), pendingTiming = ref<{ id: string; ms: number } | null>(null);
const now = ref(Date.now()), source = ref<'native_camera' | 'file'>('file');
const details = ref<Record<string, IngredientDetail>>({}), detailError = ref('');
let draftTimer: ReturnType<typeof setTimeout> | undefined;
let dirty = false, writing: Promise<void> | undefined;
const ticker = setInterval(() => { now.value = Date.now(); }, 1000);
const codes = computed(() => run.value?.exclusions.map(item => item.e_code) ?? []);
const active = computed(() => ['manual_active', 'app_active'].includes(run.value?.stage || ''));
const clock = computed(() => {
  const start = run.value?.stage === 'manual_active' ? run.value.manual_started_at : run.value?.app_started_at;
  return start ? duration(Math.max(0, now.value - Date.parse(start))) : '0 с';
});
const chosen = computed(() => run.value?.attempts.find(item => item.id === selectedAttempt.value));
const answered = computed(() => codes.value.every(code => answers.value[code]));
const step = computed(() => {
  const stage = run.value?.stage || '';
  return stage.startsWith('manual') ? 'Шаг 1 из 3 · Самостоятельная проверка' : stage.startsWith('app') ?
    (run.value?.scenario === 'comparison' ? 'Шаг 2 из 3 · Проверка ToxiCheck' : 'Шаг 1 из 2 · Проверка OCR') : 'Разметка состава';
});

async function load() {
  busy.value = true; error.value = ''; clearTimeout(draftTimer);
  try {
    await ensureGuestSession();
    const value = await request<ResearchRun>(`/research/runs/${runId.value}`);
    run.value = value;
    answers.value = { ...(value.stage === 'manual_active' ? value.manual_answers : value.app_answers) };
    external.value = value.external_search_used === null ? '' : String(value.external_search_used);
    selectedAttempt.value = value.selected_attempt_id || value.attempts.at(-1)?.id || '';
    label.value = value.user_label_text || '';
    dirty = false;
  } catch (e) { error.value = e instanceof Error ? e.message : 'Не удалось открыть проверку'; }
  finally { busy.value = false; }
}
function queueDraft(value?: Answers) {
  if (value) answers.value = value;
  dirty = true; notice.value = 'Есть несохранённые ответы';
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => { void saveDraft(); }, 650);
}
function saveDraft(): Promise<void> {
  if (writing) return writing;
  writing = (async () => {
    if (!run.value || !active.value) return;
    saving.value = true;
    try {
      while (dirty) {
        dirty = false;
        const current: ResearchRun | null = run.value;
        if (!current) return;
        const result: ResearchRun = await researchWrite<ResearchRun>(`/runs/${runId.value}/stage`, {
          revision: current.revision, action: current.stage === 'manual_active' ? 'save_manual' : 'save_app',
          answers: { ...answers.value }, external_search_used: external.value ? external.value === 'true' : null
        });
        run.value = result;
      }
      notice.value = 'Ответы сохранены';
    } catch (e) { dirty = true; error.value = e instanceof Error ? e.message : 'Ответы не сохранены'; }
    finally { saving.value = false; }
  })().finally(() => { writing = undefined; });
  return writing;
}
async function stage(action: string) {
  if (!run.value || busy.value) return;
  busy.value = true; error.value = ''; clearTimeout(draftTimer);
  try {
    if (active.value && dirty) await saveDraft();
    else if (writing) await writing;
    if (dirty && action !== 'abort') throw new Error('Сначала сохраните ответы или загрузите актуальные данные.');
    run.value = await researchWrite<ResearchRun>(`/runs/${runId.value}/stage`, {
      revision: run.value.revision, action, answers: answers.value,
      external_search_used: external.value ? external.value === 'true' : null,
      selected_attempt_id: selectedAttempt.value || null, user_label_text: label.value, skip_label: skip.value,
      reason: action === 'abort' ? 'Участник завершил проверку досрочно' : ''
    });
    answers.value = { ...(run.value.stage === 'manual_active' ? run.value.manual_answers : run.value.app_answers) };
    notice.value = ''; window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (e) { error.value = e instanceof Error ? e.message : 'Не удалось сохранить этап'; }
  finally { busy.value = false; }
}
async function selectFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  error.value = '';
  try {
    photo.value = await fileData(file); selectedFile.value = file.name;
    photoId.value = null; attemptId.value = null;
  } catch (e) { error.value = e instanceof Error ? e.message : 'Не удалось открыть фото'; }
}
function camera() { source.value = 'native_camera'; fileInput.value?.click(); }
async function sendTiming() {
  if (!pendingTiming.value) return;
  await researchWrite(`/runs/${runId.value}/attempts/${pendingTiming.value.id}/displayed`, { app_e2e_ms: pendingTiming.value.ms });
  const item = run.value?.attempts.find(item => item.id === pendingTiming.value!.id);
  if (item) { item.displayed_at = new Date().toISOString(); item.app_e2e_ms = pendingTiming.value.ms; }
  pendingTiming.value = null;
}
async function retryTiming() {
  busy.value = true; error.value = '';
  try { await sendTiming(); } catch (e) { error.value = e instanceof Error ? e.message : 'Не удалось сохранить замер'; }
  finally { busy.value = false; }
}
async function analyze() {
  if (!run.value || !photo.value || busy.value) return;
  busy.value = true; error.value = '';
  // Authorize before the measurement. Then count upload, processing and result rendering.
  try {
    clearTimeout(draftTimer);
    if (dirty) await saveDraft();
    else if (writing) await writing;
    if (dirty) throw new Error('Сначала сохраните ответы перед анализом.');
    await ensureGuestSession();
    const freshAttempt = !attemptId.value;
    const start = performance.now();
    photoId.value ||= crypto.randomUUID();
    await researchWrite(`/runs/${runId.value}/photos`, { id: photoId.value, image_base64: photo.value });
    attemptId.value ||= crypto.randomUUID();
    const result = await researchWrite<Attempt>(`/runs/${runId.value}/attempts`, { id: attemptId.value, photo_id: photoId.value, capture_source: source.value });
    const position = run.value.attempts.findIndex(item => item.id === result.id);
    if (position >= 0) run.value.attempts[position] = result; else run.value.attempts.push(result);
    selectedAttempt.value = result.id;
    await nextTick();
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    if (freshAttempt && result.status !== 'processing' && result.displayed_at === null) {
      pendingTiming.value = { id: result.id, ms: Math.round(performance.now() - start) };
      await sendTiming();
    }
    attemptId.value = null;
    notice.value = 'Результат сохранён. Изучите состав и зафиксируйте ответы.';
  } catch (e) { error.value = e instanceof Error ? e.message : 'Не удалось выполнить анализ. Загрузите состояние проверки перед повтором.'; }
  finally { busy.value = false; }
}
async function recover(attempt: Attempt) {
  busy.value = true; error.value = '';
  try { run.value = await researchWrite<ResearchRun>(`/runs/${runId.value}/attempts/${attempt.id}/recover`); }
  catch (e) { error.value = e instanceof Error ? e.message : 'Не удалось восстановить'; }
  finally { busy.value = false; }
}
async function ingredient(id: string) {
  if (details.value[id]) return;
  try { details.value[id] = await getIngredient(id); }
  catch { detailError.value = 'Не удалось загрузить описание ингредиента. Попробуйте раскрыть карточку снова.'; }
}
watch(runId, load, { immediate: true });
onBeforeUnmount(() => { clearInterval(ticker); clearTimeout(draftTimer); });
</script>
<template>
  <main class="research-shell">
    <header class="research-heading"><RouterLink to="/research">← Мои исследования</RouterLink><span v-if="run" class="research-tag">{{ stageNames[run.stage] }}</span></header>
    <p v-if="error" class="research-error" role="alert">{{ error }}<span class="research-actions"><button :disabled="busy" @click="load">Загрузить сохранённое</button><button v-if="dirty" :disabled="busy || saving" @click="saveDraft">Сохранить ответы</button><button v-if="pendingTiming" :disabled="busy" @click="retryTiming">Повторить сохранение замера</button></span></p>
    <p v-if="busy" role="status">Выполняем запрос…</p>
    <template v-if="run">
      <h1>{{ step }}</h1>
      <p v-if="active" class="research-timer">Время задания: {{ clock }}</p>
      <p class="muted" v-if="active">Таймер продолжает идти при поиске в интернете и переходе в другую вкладку. Точное время фиксируется при завершении на сервере.</p>
      <div v-if="run.stage === 'manual_ready' || run.stage === 'app_ready'" class="research-card">
        <p class="research-prewrap">{{ run.instruction }}</p>
        <p>Ваши нежелательные E-добавки: <strong>{{ codes.join(', ') || 'Не выбраны' }}</strong></p>
        <template v-if="run.stage === 'manual_ready'">
          <p>Изучите полный состав на упаковке. Обратите внимание на незнакомые ингредиенты и определите, присутствуют ли выбранные E-добавки, в том числе под альтернативными названиями.</p>
          <p>При необходимости используйте поиск в интернете и справочники. Не используйте автоматические анализаторы состава.</p>
          <button class="research-primary" :disabled="busy" @click="stage('start_manual')">Начать ручную проверку</button>
        </template>
        <template v-else><p>Теперь проверьте {{ run.scenario === 'comparison' ? 'тот же продукт' : 'продукт' }} через ToxiCheck. После распознавания изучите полный результат, включая ингредиенты и предупреждения.</p><button class="research-primary" :disabled="busy" @click="stage('start_app')">Начать проверку ToxiCheck</button></template>
      </div>
      <section v-if="run.stage === 'app_active'" class="research-card">
        <h2>Фото полного состава</h2><p>Снимите этикетку целиком и убедитесь, что текст читается. Снимок сохранится для проверки командой.</p>
        <div class="research-actions"><button :disabled="busy" @click="camera">Сфотографировать</button><label>Выбрать фото<input type="file" accept="image/jpeg,image/png,image/webp" :disabled="busy" @change="source = 'file'; selectFile($event)"></label></div>
        <input ref="fileInput" type="file" accept="image/*" capture="environment" hidden @change="selectFile">
        <img v-if="photo" :src="photo" class="research-photo" :alt="selectedFile">
        <button v-if="photo" class="research-primary" :disabled="busy || !!pendingTiming" @click="analyze">Распознать состав</button>
        <div v-if="run.attempts.length" class="research-form">
          <label>Попытка анализа<select v-model="selectedAttempt"><option v-for="(item, index) in run.attempts" :key="item.id" :value="item.id">{{ index + 1 }} · {{ attemptNames[item.status] }}</option></select></label>
          <template v-if="chosen">
            <p v-if="chosen.error" class="research-note">{{ chosen.error }}</p>
            <button v-if="!chosen.displayed_at && !pendingTiming" :disabled="busy" @click="recover(chosen)">{{ chosen.status === 'processing' ? 'Прервать зависшую попытку' : 'Продолжить с сохранённым результатом' }}</button>
            <p v-if="chosen.status === 'processing'" class="muted">После потери связи обновите состояние. Если обработка не завершилась, прервите попытку перед повтором.</p>
            <template v-if="chosen.result?.analysis">
              <h2>{{ chosen.result.analysis.verdict.title }}</h2><p>{{ chosen.result.analysis.verdict.description }}</p>
              <p v-if="chosen.result.matched_restrictions.length" class="research-error">Найдены ингредиенты из ваших исключений: {{ chosen.result.matched_restrictions.join(', ') }}</p>
              <details v-for="item in chosen.result.analysis.matched" :key="`${item.ingredient_id}-${item.raw_text}`" @toggle="($event.target as HTMLDetailsElement).open && ingredient(item.ingredient_id)">
                <summary><strong>{{ item.code }} {{ item.name }}</strong> — {{ item.raw_text }}</summary>
                <p v-if="details[item.ingredient_id]">{{ details[item.ingredient_id]!.full_description || details[item.ingredient_id]!.description }}</p>
                <p v-for="rule in item.rules" :key="rule.id"><strong>{{ rulePresentation(rule).title }}.</strong> {{ rulePresentation(rule).summary }}</p>
              </details>
              <p v-if="detailError" class="research-error">{{ detailError }}</p>
              <p v-if="chosen.result.analysis.unmatched.length">Не удалось определить: {{ chosen.result.analysis.unmatched.join(', ') }}</p>
            </template>
            <details v-if="chosen.result?.raw_ingredient_text"><summary>Распознанный текст</summary><p class="research-prewrap">{{ chosen.result.raw_ingredient_text }}</p></details>
          </template>
        </div>
      </section>
      <section v-if="active" class="research-card">
        <h2>Какие добавки присутствуют?</h2><p>Учитывайте альтернативные названия. Завершайте проверку после изучения всего состава.</p>
        <ResearchAnswers :codes="codes" :model-value="answers" :disabled="busy" @update:model-value="queueDraft" />
        <label v-if="run.stage === 'manual_active'">Использовали поиск в интернете?<select v-model="external" :disabled="busy" @change="queueDraft()"><option value="">Выберите ответ</option><option value="false">Нет</option><option value="true">Да</option></select></label>
        <p class="muted" role="status">{{ saving ? 'Сохраняем ответы…' : notice }}</p>
        <button class="research-primary" :disabled="busy || !answered || (run.stage === 'manual_active' ? !external : !chosen?.displayed_at)" @click="stage(run.stage === 'manual_active' ? 'finish_manual' : 'finish_app')">Завершить проверку и остановить таймер</button>
      </section>
      <form v-if="run.stage === 'labeling'" class="research-card research-form" @submit.prevent="stage('annotate')">
        <h2>Перенесите состав с этикетки</h2><p>Таймеры остановлены. Введите состав так, как он указан производителем: не заменяйте названия E-кодами и не исправляйте формулировки.</p>
        <p>Разметка необязательна. Ваши ответы уже сохранены.</p>
        <a v-for="item in run.photos" :key="item.id" :href="photoUrl(run.id, item.id)" target="_blank" rel="noopener"><img class="research-photo" :src="photoUrl(run.id, item.id)" alt="Исходная этикетка — открыть крупнее"></a>
        <label>Полный состав<textarea v-model="label" maxlength="30000" :disabled="skip || busy" :required="!skip" placeholder="Введите текст с оригинальной этикетки"></textarea></label>
        <label class="research-check"><input v-model="skip" type="checkbox" :disabled="busy">Пропустить разметку</label>
        <button class="research-primary" :disabled="busy">Сохранить и завершить исследование</button>
      </form>
      <section v-if="['completed','aborted'].includes(run.stage)" class="research-card"><h2>{{ run.stage === 'completed' ? 'Спасибо за участие!' : 'Проверка прервана' }}</h2><p>Сохранённые этапы доступны команде исследования.</p><p>Ручная проверка: {{ duration(run.manual_duration_ms) }}. ToxiCheck: {{ duration(run.app_duration_ms) }}.</p><RouterLink to="/research">Проверить следующий продукт →</RouterLink></section>
      <details v-else class="research-card"><summary>Завершить участие досрочно</summary><p>Уже сохранённые данные останутся в исследовании. Проверка будет отмечена как прерванная.</p><button class="research-danger" :disabled="busy" @click="stage('abort')">Прервать эту проверку</button></details>
    </template>
  </main>
</template>
