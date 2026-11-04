<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { apiBaseUrl, request } from '@/services/http';
import { refreshSession } from '@/services/userSession';
import { researchWrite, photoUrl, duration, stageNames, validationNames, answerNames, attemptNames,
  type Study, type RunSummary, type ResearchRun, type Validation } from '@/services/research';
import '@/research.css';

const authorized = ref(false), busy = ref(false), error = ref(''), message = ref('');
const studies = ref<Study[]>([]), rows = ref<RunSummary[]>([]), total = ref(0), offset = ref(0);
const studyId = ref(''), participantId = ref(''), caseId = ref(''), stage = ref(''), validationStatus = ref(''), since = ref(''), until = ref(''), errorsOnly = ref(false);
const title = ref(''), scenario = ref('comparison'), protocol = ref('1');
const instruction = ref('Изучите полный состав продукта. Обратите внимание на E-добавки и незнакомые ингредиенты. Определите, присутствуют ли E-добавки из ваших исключений, в том числе под альтернативными названиями.');
const detail = ref<ResearchRun | null>(null), reveal = ref(false), linkCase = ref('');
const review = ref<Validation | null>(null), codes = ref(''), includePhotos = ref(false);
const filterQuery = computed(() => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ study_id: studyId.value, participant_id: participantId.value, case_id: caseId.value,
    stage: stage.value, validation: validationStatus.value })) if (value) params.set(key, value);
  if (since.value) params.set('since', new Date(since.value).toISOString());
  if (until.value) params.set('until', new Date(`${until.value}T23:59:59.999`).toISOString());
  if (errorsOnly.value) params.set('errors', 'true');
  return params;
});
const originalAnswers = computed(() => detail.value?.exclusions.map(item => ({ code: item.e_code,
  manual: detail.value?.manual_answers?.[item.e_code], app: detail.value?.app_answers?.[item.e_code] })) ?? []);

async function task(fn: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true; error.value = ''; message.value = '';
  try { await fn(); } catch (e) { error.value = e instanceof Error ? e.message : 'Не удалось выполнить запрос'; }
  finally { busy.value = false; }
}
async function loadRows(reset = true) {
  if (reset) offset.value = 0;
  const params = new URLSearchParams(filterQuery.value);
  params.set('offset', String(offset.value)); params.set('limit', '30');
  const page = await request<{ items: RunSummary[]; total: number }>(`/admin/research/runs?${params}`);
  rows.value = page.items; total.value = page.total;
}
async function initialize() {
  await task(async () => {
    const state = await refreshSession();
    if (!state.admin) return;
    await request('/admin/me'); authorized.value = true;
    studies.value = await request<Study[]>('/admin/research/studies');
    await loadRows();
  });
}
async function createStudy() {
  await task(async () => {
    await researchWrite('/studies', { title: title.value, instruction: instruction.value, scenario: scenario.value, protocol_version: protocol.value }, true);
    studies.value = await request<Study[]>('/admin/research/studies'); title.value = '';
    message.value = 'Исследование создано закрытым. Проверьте инструкцию и откройте участие.';
  });
}
async function toggleStudy(item: Study) {
  await task(async () => {
    await researchWrite(`/studies/${item.id}/state`, { is_open: !item.is_open }, true, 'PUT');
    item.is_open = !item.is_open;
  });
}
async function openRun(id: string, showResults = false) {
  await task(async () => {
    const preserveDraft = showResults && detail.value?.id === id;
    const value = await request<ResearchRun>(`/admin/research/runs/${id}?reveal=${showResults}`);
    detail.value = value; reveal.value = showResults;
    if (!preserveDraft) {
      review.value = value.validation ? { ...value.validation } : null;
      codes.value = value.validation?.ground_truth_e_codes.join(', ') || '';
      linkCase.value = value.case_id;
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
}
async function saveReview() {
  if (!detail.value || !review.value) return;
  await task(async () => {
    const data = await researchWrite<ResearchRun>(`/runs/${detail.value!.id}/validation`, {
      revision: review.value!.revision, status: review.value!.status, verified_label_text: review.value!.verified_label_text,
      ground_truth_e_codes: codes.value.split(/[,;\n]+/).map(x => x.trim()).filter(Boolean), comment: review.value!.comment
    }, true, 'PUT');
    detail.value = data; review.value = { ...data.validation! }; reveal.value = false;
    codes.value = data.validation!.ground_truth_e_codes.join(', ');
    message.value = 'Эталон сохранён. Действие записано в журнал.';
  });
}
async function exportData() {
  await task(async () => {
    const params = new URLSearchParams(filterQuery.value); params.set('include_photos', String(includePhotos.value));
    const response = await fetch(`${apiBaseUrl}/admin/research/export?${params}`, { credentials: 'include', cache: 'no-store' });
    if (!response.ok) { const data = await response.json().catch(() => null); throw new Error(data?.detail || 'Выгрузка не выполнена'); }
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'toxicheck-research.zip'; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  });
}
function shareLink(id: string) { return `${window.location.origin}/research?study=${id}`; }
async function link() {
  if (!detail.value) return;
  await task(async () => {
    await researchWrite(`/runs/${detail.value!.id}/case`, { case_id: linkCase.value }, true, 'PUT');
    detail.value!.case_id = linkCase.value; message.value = 'Связь с этикеткой обновлена.';
  });
}
onMounted(initialize);
</script>
<template>
  <main class="research-shell">
    <header class="research-heading"><RouterLink to="/app/admin">← Админ-режим</RouterLink><span class="research-tag">Кабинет исследователя</span></header>
    <h1>Исследования ToxiCheck</h1>
    <p v-if="busy" role="status">Выполняем запрос…</p><p v-if="error" role="alert" class="research-error">{{ error }}</p><p v-if="message" role="status" class="research-note">{{ message }}</p>
    <p v-if="!authorized && !busy">Для просмотра нужен <RouterLink to="/app/admin">вход администратора</RouterLink>. <button @click="initialize">Проверить доступ</button></p>
    <template v-if="authorized">
      <template v-if="detail">
        <button :disabled="busy" @click="detail = null; task(() => loadRows(false))">← К списку проверок</button>
        <section class="research-card"><h2>{{ detail.case_title }}</h2><p>{{ detail.study_title }} · {{ stageNames[detail.stage] }}</p>
          <p class="muted research-prewrap">Проверка: {{ detail.id }}<br>Участник: {{ detail.participant_id }}<br>Этикетка: {{ detail.case_id }}<br>Протокол: {{ detail.protocol_version }}</p>
          <p>Ручной этап: {{ duration(detail.manual_duration_ms) }} · ToxiCheck: {{ duration(detail.app_duration_ms) }}</p>
          <p>Исключения: {{ detail.exclusions.map(x => x.e_code).join(', ') || 'Нет' }}</p>
        </section>
        <div class="research-grid">
          <section class="research-card"><h2>Исходная этикетка</h2>
            <p v-if="!detail.photos.length">Фотография не загружена. Подтвердить эталон нельзя.</p>
            <a v-for="photo in detail.photos" :key="photo.id" :href="photoUrl(detail.id, photo.id, true)" target="_blank" rel="noopener"><img class="research-photo" :src="photoUrl(detail.id, photo.id, true)" :alt="`Этикетка ${photo.id}`"><small>{{ photo.id }}</small></a>
            <h3>Перенос состава участником</h3><p class="research-prewrap">{{ detail.user_label_text || (detail.labeling_status === 'skipped' ? 'Участник пропустил разметку' : 'Разметка ещё не выполнена') }}</p>
          </section>
          <form v-if="review" class="research-card research-form" @submit.prevent="saveReview">
            <h2>Проверка эталона</h2><p class="muted">Сверьте текст с фотографией. Ответы участника и результат алгоритма скрыты до отдельного раскрытия.</p>
            <label>Статус<select v-model="review.status"><option v-for="(name,key) in validationNames" :key="key" :value="key">{{ name }}</option></select></label>
            <label>Подтверждённый текст состава<textarea v-model="review.verified_label_text" maxlength="30000"></textarea></label>
            <label>Все эталонные E-коды через запятую<input v-model="codes" placeholder="E202, E211"></label>
            <p class="muted">Укажите и добавки, написанные на этикетке синонимами. Пустой список допустим, если E-добавок действительно нет.</p>
            <label>Комментарий<textarea v-model="review.comment" maxlength="10000"></textarea></label>
            <button class="research-primary" :disabled="busy">Сохранить эталон</button>
            <small v-if="review.reviewer_id">Проверяющий: {{ review.reviewer_id }} · {{ new Date(review.updated_at).toLocaleString('ru') }}</small>
          </form>
        </div>
        <section class="research-card"><h2>Ответы и автоматический анализ</h2>
          <button v-if="!reveal" :disabled="busy" @click="openRun(detail.id, true)">Раскрыть ответы и результат ToxiCheck</button>
          <template v-else>
            <div v-for="answer in originalAnswers" :key="answer.code" class="research-list-row"><strong>{{ answer.code }}</strong><span>Вручную: {{ answer.manual ? answerNames[answer.manual] : 'Нет ответа' }}</span><span>ToxiCheck: {{ answer.app ? answerNames[answer.app] : 'Нет ответа' }}</span></div>
            <p>Поиск в интернете: {{ detail.external_search_used === null ? 'Нет ответа' : detail.external_search_used ? 'Да' : 'Нет' }}</p>
          </template>
          <details v-for="(attempt,index) in detail.attempts" :key="attempt.id"><summary>Попытка {{ index + 1 }} · {{ attemptNames[attempt.status] }} {{ detail.selected_attempt_id === attempt.id ? '· Выбрана участником' : '' }}</summary>
            <p>App E2E: {{ duration(attempt.app_e2e_ms) }} · Сервер: {{ duration(attempt.server_processing_ms) }}</p>
            <p v-if="attempt.error" class="research-error">{{ attempt.error }}</p>
            <p class="muted research-prewrap">Фото: {{ attempt.photo_id }}<br>Приложение: {{ attempt.app_version }}<br>Алгоритм: {{ attempt.pipeline_version }}<br>Каталог: {{ attempt.catalog_version }}</p>
            <template v-if="reveal"><p>Найдены E-коды: {{ attempt.result?.app_detected_e_codes.join(', ') || 'Нет' }}</p><p>Совпали с исключениями: {{ attempt.result?.matched_restrictions.join(', ') || 'Нет' }}</p><pre>{{ JSON.stringify(attempt.result, null, 2) }}</pre></template>
          </details>
        </section>
        <details class="research-card"><summary>Связать с другой проверкой той же этикетки</summary><p>Только после сравнения фотографий. Один штрихкод не гарантирует одинаковый состав.</p><label>case_id существующей этикетки<input v-model="linkCase"></label><button :disabled="busy" @click="link">Связать</button></details>
        <details class="research-card"><summary>Журнал изменений</summary><div v-for="event in detail.audit" :key="event.id" class="research-list-row"><span>{{ event.event }}<small>{{ new Date(event.created_at).toLocaleString('ru') }} · {{ event.actor_id }}</small></span><details><summary>Подробности</summary><pre>{{ JSON.stringify(event.details, null, 2) }}</pre></details></div></details>
      </template>
      <template v-else>
        <details class="research-card"><summary>Исследования и ссылки участникам</summary>
          <div v-for="item in studies" :key="item.id" class="research-card"><h3>{{ item.title }}</h3><p>{{ item.scenario === 'comparison' ? 'Ручная проверка → OCR → разметка' : 'OCR → разметка' }} · версия {{ item.protocol_version }}</p><p class="research-prewrap">{{ item.instruction }}</p><label>Ссылка участнику<input :value="shareLink(item.id)" readonly @focus="($event.target as HTMLInputElement).select()"></label><button :disabled="busy" @click="toggleStudy(item)">{{ item.is_open ? 'Закрыть для новых участников' : 'Открыть участие' }}</button></div>
          <form class="research-form" @submit.prevent="createStudy"><h2>Новое исследование</h2><label>Название<input v-model="title" maxlength="200" required></label><label>Сценарий<select v-model="scenario"><option value="comparison">Ручная проверка → OCR → разметка</option><option value="ocr_quality">OCR → разметка</option></select></label><label>Версия протокола<input v-model="protocol" maxlength="40" required></label><label>Инструкция участнику<textarea v-model="instruction" maxlength="10000" required></textarea></label><p class="muted">Для изменения условий создайте новую версию исследования. Условия начатых проверок сохраняются.</p><button class="research-primary" :disabled="busy">Создать исследование</button></form>
        </details>
        <form class="research-card research-form" @submit.prevent="task(() => loadRows())"><h2>Проверки участников</h2>
          <div class="research-grid"><label>Исследование<select v-model="studyId"><option value="">Все</option><option v-for="item in studies" :key="item.id" :value="item.id">{{ item.title }}</option></select></label><label>Этап<select v-model="stage"><option value="">Все</option><option v-for="(name,key) in stageNames" :key="key" :value="key">{{ name }}</option></select></label><label>Проверка эталона<select v-model="validationStatus"><option value="">Все</option><option v-for="(name,key) in validationNames" :key="key" :value="key">{{ name }}</option></select></label><label>ID участника<input v-model="participantId" placeholder="Все участники"></label><label>case_id<input v-model="caseId" placeholder="Все этикетки"></label><label>С даты<input v-model="since" type="date"></label><label>По дату<input v-model="until" type="date"></label></div>
          <label class="research-check"><input v-model="errorsOnly" type="checkbox">Только с ошибками, прерываниями или повторными снимками</label><button :disabled="busy">Применить фильтры</button>
        </form>
        <p>Найдено проверок: {{ total }}</p>
        <button v-for="row in rows" :key="row.id" class="research-card research-choice" style="width:100%" :disabled="busy" @click="openRun(row.id)"><strong>{{ row.case_title }}</strong><span>{{ row.study_title }} · {{ stageNames[row.stage] }} · {{ validationNames[row.validation_status || 'unreviewed'] }}</span><small>Участник {{ row.participant_id }} · {{ new Date(row.created_at).toLocaleString('ru') }} · Фото: {{ row.photo_count }}</small></button>
        <div class="research-actions"><button :disabled="busy || offset === 0" @click="offset -= 30; task(() => loadRows(false))">Предыдущие</button><span>{{ total ? offset + 1 : 0 }}–{{ Math.min(offset + 30, total) }} из {{ total }}</span><button :disabled="busy || offset + 30 >= total" @click="offset += 30; task(() => loadRows(false))">Следующие</button></div>
        <section class="research-card"><h2>Выгрузка по выбранным фильтрам</h2><p>ZIP: CSV, Excel, полные данные JSON и список фотографий. Итоговые метрики рассчитываются после выгрузки.</p><label class="research-check"><input v-model="includePhotos" type="checkbox">Включить исходные фотографии</label><button class="research-primary" :disabled="busy" @click="exportData">Скачать архив</button></section>
      </template>
    </template>
  </main>
</template>
