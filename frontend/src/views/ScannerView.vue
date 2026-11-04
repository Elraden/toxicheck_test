<script setup lang="ts">
import {
  computed,
  onBeforeUnmount,
  onMounted,
  ref,
  watch
} from 'vue';

import { storeToRefs } from 'pinia';
import {
  useRoute,
  useRouter
} from 'vue-router';

import { ArrowLeft, RotateCcw, RotateCw, Smartphone, RectangleHorizontal } from 'lucide-vue-next';
import { prepareLabelImage } from '@/services/imageOrientation';

import CameraControls from '@/components/CameraControls.vue';

import { useCameraScanner } from '@/composables/useCameraScanner';
import { useScannerStore } from '@/stores/scanner';

import { getProductByBarcode } from '@/services/openFoodFacts';
import {
  scanCompositionImage,
  type CompositionScanResponse
} from '@/services/backendApi';

import type { ScanMode } from '@/types/camera';

const route = useRoute();
const router = useRouter();

const scannerStore = useScannerStore();
const nativeCameraInput =
  ref<HTMLInputElement | null>(null);
const orientation = ref<'landscape' | 'portrait'>('landscape');
const pendingImage = ref('');
const preparingImage = ref(false);
const pendingSource = ref<'browser_camera' | 'native_camera'>('browser_camera');
let originalImage = '';
let rotation = 0;
let imageVersion = 0;

const {
  mode,
  status,
  message,

  barcode,
  capturedImage,

  scannerStarted,
  scanAttempts,
  scannerMessage,
  cameraResolution,

  zoom,
  torchEnabled
} = storeToRefs(scannerStore);

const {
  videoElement,

  canZoom,
  canUseTorch,

  zoomMin,
  zoomMax,
  zoomStep,

  openCamera,
  startBarcodeScanning,
  stopCamera,

  setZoom,
  toggleTorch,

  captureCurrentFrame
} = useCameraScanner();

const modeSwitchDisabled = computed(() => {
  return (
    preparingImage.value || status.value === 'starting' ||
    status.value === 'loading'
  );
});

let componentMounted = false;
let scanSession = 0;

function getModeFromRoute(): ScanMode {
  return route.query.mode === 'text'
    ? 'text'
    : 'barcode';
}

scannerStore.setMode(getModeFromRoute());

function selectMode(value: ScanMode): void {
  if (
    modeSwitchDisabled.value ||
    mode.value === value
  ) {
    return;
  }

  scannerStore.setMode(value);
}

function handleVideoReady(event: Event): void {
  const video =
    event.currentTarget as HTMLVideoElement;

  scannerStore.setCameraResolution(
    `${video.videoWidth} × ${video.videoHeight}`
  );
}

function handleCameraError(error: unknown): void {
  console.error(
    'Ошибка запуска сканера:',
    error
  );

  if (
    error instanceof DOMException &&
    error.name === 'NotAllowedError'
  ) {
    scannerStore.setStatus(
      'error',
      'Доступ к камере запрещён'
    );

    return;
  }

  if (
    error instanceof DOMException &&
    error.name === 'NotFoundError'
  ) {
    scannerStore.setStatus(
      'error',
      'Камера на устройстве не найдена'
    );

    return;
  }

  scannerStore.setStatus(
    'error',

    error instanceof Error
      ? error.message
      : 'Не удалось запустить камеру'
  );
}

async function loadProduct(
  detectedCode: string,
  session: number
): Promise<void> {
  if (session !== scanSession) {
    return;
  }

  scannerStore.setBarcode(detectedCode);

  scannerStore.setStatus(
    'loading',
    `Штрихкод ${detectedCode} найден. Загружаем товар…`
  );

  try {
    const product =
      await getProductByBarcode(
        detectedCode
      );

    if (session !== scanSession) {
      return;
    }

    scannerStore.setProductJson(product);

    scannerStore.setStatus(
      'done',
      'Ответ Open Food Facts получен'
    );

    void router.push({
      name: 'scan-result',
      params: {
        barcode: detectedCode
      }
    });
  } catch (error) {
    if (session !== scanSession) {
      return;
    }

    scannerStore.setStatus(
      'error',

      error instanceof Error
        ? error.message
        : 'Не удалось загрузить товар'
    );
  }
}

function createCompositionProduct(
  result: CompositionScanResponse
): Record<string, unknown> {
  const ingredientsText =
    result.ingredientsText?.trim() ?? '';
  const allergensText =
    result.allergensText?.trim() ?? '';

  return {
    status: 'success',
    code: '',
    product: {
      code: '',
      product_name: 'Состав с изображения',
      ingredients_text: ingredientsText,
      allergens: allergensText
    },
    ocr: {
      request_id: result.requestId ?? crypto.randomUUID(),
      tokens: result.tokens ?? [],
      status: result.status,
      capture_source:
        result.captureSource ?? 'unknown',
      raw_text: result.recognizedText ?? '',
      composition_text: ingredientsText,
      allergens_text: allergensText,
      confidence: result.confidence ?? null,
      processing_time_ms:
        result.processingTimeMs ?? null
    },
    result: {
      id: ingredientsText
        ? 'composition_found'
        : 'composition_empty',
      name: ingredientsText
        ? 'Composition found'
        : 'Composition was not extracted'
    }
  };
}

function readImageFileAsDataUrl(
  file: File
): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();

    reader.onerror = () => {
      reject(
        new Error('Не удалось прочитать изображение')
      );
    };

    reader.onload = () => {
      if (typeof reader.result === 'string') {
        resolve(reader.result);
        return;
      }

      reject(
        new Error('Не удалось получить изображение')
      );
    };

    reader.readAsDataURL(file);
  });
}

async function submitCompositionImage(
  image: string,
  session: number,
  captureSource: 'browser_camera' | 'native_camera'
): Promise<void> {
  scannerStore.setCapturedImage(image);

  scannerStore.setStatus(
    'loading',
    'Отправляем состав на распознавание...'
  );

  const result =
    await scanCompositionImage(
      image,
      captureSource
    );

  console.info(
    'OCR composition scan result',
    result
  );

  if (session !== scanSession) {
    return;
  }

  if (!['success', 'needs_retake'].includes(result.status)) {
    scannerStore.setStatus(
      'error',
      result.message ||
      'Не удалось распознать состав'
    );

    return;
  }

  scannerStore.setProductJson(
    createCompositionProduct(result)
  );

  scannerStore.setStatus(
    'done',
    result.ingredientsText?.trim()
      ? 'Состав распознан'
      : 'Состав не определен'
  );

  void router.push({
    name: 'scan-result'
  });
}

async function startSelectedMode():
  Promise<void> {
  const currentSession = ++scanSession;

  imageVersion++;
  pendingImage.value = '';
  originalImage = '';
  preparingImage.value = false;

  stopCamera();
  scannerStore.resetForNewScan();

  scannerStore.setStatus(
    'starting',
    'Запускаем камеру…'
  );

  try {
    if (mode.value === 'barcode') {
      await startBarcodeScanning(
        (detectedCode) =>
          loadProduct(
            detectedCode,
            currentSession
          )
      );

      if (currentSession !== scanSession) {
        stopCamera();
        return;
      }

      scannerStore.setStatus(
        'scanning',
        'Наведите камеру на штрихкод'
      );

      return;
    }

    await openCamera(orientation.value);

    if (currentSession !== scanSession) {
      stopCamera();
      return;
    }

    scannerStore.setStatus(
      'scanning',
      'Наведите камеру на состав продукта'
    );
  } catch (error) {
    if (currentSession !== scanSession) {
      return;
    }

    handleCameraError(error);
  }
}

async function captureText(): Promise<void> {
  const currentSession = scanSession;

  try {
    const image =
      captureCurrentFrame();

    stopCamera();

    await previewImage(image, 'browser_camera');
  } catch (error) {
    if (currentSession !== scanSession) {
      return;
    }

    scannerStore.setStatus(
      'error',

      error instanceof Error
        ? error.message
        : 'Не удалось обработать изображение'
    );
  }
}

function captureTextWithNativeCamera(): void {
  if (
    mode.value !== 'text' ||
    modeSwitchDisabled.value
  ) {
    return;
  }

  nativeCameraInput.value?.click();
}

async function handleNativeCameraChange(
  event: Event
): Promise<void> {
  const input =
    event.currentTarget as HTMLInputElement;
  const file = input.files?.[0];

  input.value = '';

  if (!file) {
    return;
  }

  const currentSession = scanSession;

  try {
    const image =
      await readImageFileAsDataUrl(file);

    if (currentSession !== scanSession) return;

    stopCamera();

    await previewImage(image, 'native_camera');
  } catch (error) {
    if (currentSession !== scanSession) {
      return;
    }

    scannerStore.setStatus(
      'error',

      error instanceof Error
        ? error.message
        : 'Не удалось обработать изображение'
    );
  }
}

function retryScanning(): void {
  void startSelectedMode();
}

async function previewImage(image: string, source: 'browser_camera' | 'native_camera'): Promise<void> {
  originalImage = image;
  rotation = 0;
  pendingSource.value = source;
  scannerStore.setStatus('idle', 'Фото состава');
  await rotatePreview(0);
}

async function rotatePreview(turns: number): Promise<void> {
  const version = ++imageVersion;
  const nextRotation = rotation + turns;
  preparingImage.value = true;
  try {
    const image = await prepareLabelImage(originalImage, nextRotation);
    if (version !== imageVersion) return;
    rotation = nextRotation;
    pendingImage.value = image;
    scannerStore.setCapturedImage(image);
  } catch {
    if (version === imageVersion) scannerStore.setStatus('error', 'Не удалось открыть фото. Попробуйте JPEG или PNG.');
  } finally {
    if (version === imageVersion) preparingImage.value = false;
  }
}

async function confirmImage(): Promise<void> {
  if (!pendingImage.value || modeSwitchDisabled.value) return;
  const session = scanSession;
  try {
    await submitCompositionImage(pendingImage.value, session, pendingSource.value);
  } catch (error) {
    if (session === scanSession) scannerStore.setStatus('error', error instanceof Error ? error.message : 'Не удалось распознать состав');
  }
}

function selectOrientation(value: 'landscape' | 'portrait'): void {
  if (modeSwitchDisabled.value || pendingImage.value || value === orientation.value) return;
  orientation.value = value;
  void startSelectedMode();
}

function handleZoomChange(
  value: number
): void {
  void setZoom(value);
}

function handleTorchToggle(): void {
  void toggleTorch();
}

function goBack(): void {
  if (window.history.length > 1) {
    router.back();
    return;
  }

  void router.push({
    name: 'home'
  });
}

watch(
  mode,

  () => {
    if (componentMounted) {
      void startSelectedMode();
    }
  }
);

watch(
  () => route.query.mode,

  () => {
    const routeMode =
      getModeFromRoute();

    if (routeMode !== mode.value) {
      scannerStore.setMode(routeMode);
    }
  }
);

onMounted(() => {
  componentMounted = true;

  void startSelectedMode();
});

onBeforeUnmount(() => {
  componentMounted = false;

  // Делает незавершённые запросы неактуальными.
  scanSession += 1;
  imageVersion++;

  stopCamera();
});
</script>

<template>
  <section class="scanner" :class="{ 'scanner--text': mode === 'text' }">
    <header class="scanner__header">
      <button
        type="button"
        class="scanner__back"
        aria-label="Назад"
        @click="goBack"
      >
        <ArrowLeft aria-hidden="true" />
      </button>

      <h1 class="scanner__title">
        Сканирование
      </h1>

      <div class="scanner__modes">
        <button type="button" class="scanner__mode" :class="{
          'scanner__mode--active':
            mode === 'barcode'
        }" :disabled="modeSwitchDisabled" @click="selectMode('barcode')">
          Штрихкод
        </button>

        <button type="button" class="scanner__mode" :class="{
          'scanner__mode--active':
            mode === 'text'
        }" :disabled="modeSwitchDisabled" @click="selectMode('text')">
          Состав
        </button>
      </div>
    <div v-if="mode === 'text' && !pendingImage" class="scanner__orientation" role="group" aria-label="Ориентация фото">
      <button type="button" :aria-pressed="orientation === 'landscape'" :disabled="modeSwitchDisabled" title="Горизонтально" aria-label="Горизонтально" @click="selectOrientation('landscape')"><RectangleHorizontal /></button>
      <button type="button" :aria-pressed="orientation === 'portrait'" :disabled="modeSwitchDisabled" title="Вертикально" aria-label="Вертикально" @click="selectOrientation('portrait')"><Smartphone /></button>
    </div>
    </header>
    <div class="scanner__camera" :class="{ 'scanner__camera--wide': mode === 'text' && orientation === 'landscape' }">
      <video ref="videoElement" class="scanner__video" autoplay muted playsinline @loadedmetadata="handleVideoReady" @resize="handleVideoReady" />

      <img v-if="capturedImage" class="scanner__preview" :src="capturedImage" alt="Снимок состава продукта">

      <div v-if="status === 'scanning'" class="scanner__frame" :class="{
        'scanner__frame--barcode':
          mode === 'barcode',

        'scanner__frame--text':
          mode === 'text'
      }" />

      <CameraControls v-if="status === 'scanning'" :can-zoom="canZoom" :zoom="zoom" :zoom-min="zoomMin"
        :zoom-max="zoomMax" :zoom-step="zoomStep" :can-use-torch="canUseTorch" :torch-enabled="torchEnabled"
        @zoom-change="handleZoomChange" @torch-toggle="handleTorchToggle" />

      <div v-if="
        status === 'starting' ||
        status === 'loading'
      " class="scanner__overlay">
        {{ message }}
      </div>
    </div>

    <div class="scanner__actions">
    <p class="scanner__message" role="status">
      {{ message }}
    </p>

    <button v-if="
      mode === 'text' &&
      status === 'scanning'
    " type="button" class="scanner__action" @click="captureText">
      Сфотографировать состав
    </button>

    <button v-if="
      mode === 'text' &&
      !pendingImage && !modeSwitchDisabled
    " type="button" class="scanner__action scanner__action--secondary" @click="captureTextWithNativeCamera">
      Сфотографировать состав нативной камерой
    </button>

    <template v-if="pendingImage">
      <div class="scanner__orientation" role="group" aria-label="Поворот фото">
        <button type="button" :disabled="modeSwitchDisabled" aria-label="Повернуть влево" title="Повернуть влево" @click="rotatePreview(-1)"><RotateCcw /></button>
        <button type="button" :disabled="modeSwitchDisabled" aria-label="Повернуть вправо" title="Повернуть вправо" @click="rotatePreview(1)"><RotateCw /></button>
      </div>
      <button type="button" class="scanner__action" :disabled="modeSwitchDisabled" @click="confirmImage">Распознать состав</button>
      <button type="button" class="scanner__action scanner__action--secondary" :disabled="modeSwitchDisabled" @click="retryScanning">Переснять</button>
    </template>
    <button v-else-if="status === 'error'" type="button" class="scanner__action" @click="retryScanning">Попробовать снова</button>
    </div>

    <input
      ref="nativeCameraInput"
      class="scanner__native-input"
      type="file"
      accept="image/*"
      capture="environment"
      @change="handleNativeCameraChange"
    >

    <section class="scanner-debug">
      <h2 class="scanner-debug__title">
        Диагностика
      </h2>

      <p>
        ZXing:
        <strong>
          {{
            scannerStarted
              ? 'запущен'
              : 'не запущен'
          }}
        </strong>
      </p>

      <p>
        Камера:
        <strong>
          {{ cameraResolution }}
        </strong>
      </p>

      <p>
        Проверено кадров:
        <strong>
          {{ scanAttempts }}
        </strong>
      </p>

      <p>
        Результат:
        <strong>
          {{ barcode || 'код не найден' }}
        </strong>
      </p>

      <p>
        Увеличение:
        <strong>
          {{ zoom.toFixed(1) }}×
        </strong>
      </p>

      <p>
        Фонарик:
        <strong>
          {{
            canUseTorch
              ? torchEnabled
                ? 'включён'
                : 'выключен'
              : 'не поддерживается'
          }}
        </strong>
      </p>

      <p>
        Состояние:
        <strong>
          {{ scannerMessage }}
        </strong>
      </p>
    </section>


  </section>
</template>

<style scoped>
.scanner {
  min-height: calc(100dvh - 72px);
  box-sizing: border-box;
  padding: 20px;
  background-color: #f6f8f7;
}

.scanner__header {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr);
  align-items: center;
  gap: 12px 4px;
  margin-bottom: 20px;
}

.scanner__back {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border: 0;
  border-radius: 8px;
  color: #17201d;
  background: transparent;
  cursor: pointer;
}

.scanner__back svg {
  width: 22px;
  height: 22px;
  stroke-width: 2;
}

.scanner__title {
  margin: 0;
  color: #17201d;
  font-size: 24px;
}

.scanner__modes {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  padding: 4px;
  border-radius: 12px;
  background-color: #e7ecea;
}

.scanner__mode {
  min-height: 42px;
  border: 0;
  border-radius: 9px;
  color: #66736e;
  background: transparent;
  font: inherit;
  cursor: pointer;
}

.scanner__mode--active {
  color: #ffffff;
  background-color: #25a777;
}

.scanner__mode:disabled {
  cursor: default;
  opacity: 0.6;
}

.scanner__camera {
  position: relative;
  overflow: hidden;
  aspect-ratio: 3 / 4;
  border-radius: 18px;
  background-color: #17201d;
}

.scanner__video,
.scanner__preview {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.scanner__preview {
  position: absolute;
  inset: 0;
  z-index: 1;
}

.scanner__frame {
  position: absolute;
  z-index: 2;
  top: 46%;
  left: 50%;
  border: 2px solid #51b992;
  border-radius: 14px;
  box-shadow:
    0 0 0 999px rgb(0 0 0 / 18%);
  transform: translate(-50%, -50%);
  pointer-events: none;
}

.scanner__frame--barcode {
  width: 82%;
  height: 25%;
}

.scanner__frame--text {
  width: 84%;
  height: 58%;
}

.scanner__overlay {
  position: absolute;
  z-index: 5;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 24px;
  color: #ffffff;
  text-align: center;
  background-color: rgb(0 0 0 / 55%);
}

.scanner__message {
  min-height: 24px;
  margin: 16px 0;
  color: #66736e;
  text-align: center;
}

.scanner__action {
  width: 100%;
  min-height: 48px;
  margin-top: 10px;
  border: 0;
  border-radius: 12px;
  color: #ffffff;
  background-color: #25a777;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.scanner__action--secondary {
  border: 1px solid #25a777;
  color: #1c8f66;
  background-color: #eefaf5;
}

.scanner__native-input {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

.scanner-debug {
  margin: 16px 0;
  padding: 14px;
  border: 1px solid #dfe6e3;
  border-radius: 12px;
  color: #56625e;
  background-color: #ffffff;
  font-size: 13px;
}

.scanner-debug__title {
  margin: 0 0 10px;
  color: #17201d;
  font-size: 15px;
}

.scanner-debug p {
  margin: 6px 0;
}

</style>

<style scoped>
.scanner__orientation { display: flex; gap: 8px; margin-bottom: 12px; }
.scanner__header > .scanner__orientation { grid-column: 1 / -1; margin-bottom: 0; }
.scanner__orientation button { display: grid; place-items: center; width: 44px; height: 44px; border: 1px solid #cbd8d3; border-radius: 8px; color: #394b44; background: white; cursor: pointer; }
.scanner__orientation button[aria-pressed="true"] { color: #176c56; border-color: #19845f; background: #e9f6f1; }
.scanner__action { padding: 10px; overflow-wrap: anywhere; }
button:disabled { opacity: .55; cursor: default; }
.scanner--text .scanner__video, .scanner__preview { object-fit: contain; }
.scanner__camera--wide { aspect-ratio: 4 / 3; }
.scanner--text .scanner__frame { display: none; }
@media (orientation: landscape) and (max-height: 600px) {
  .scanner--text { display: grid; grid-template-columns: minmax(0, 1fr) minmax(220px, .6fr); gap: 10px 16px; padding: 12px 20px; align-items: start; }
  .scanner__header { grid-column: 1 / -1; margin-bottom: 0; grid-template-columns: 40px minmax(0, 1fr) auto 210px; }
  .scanner__title { font-size: 20px; }
  .scanner__modes { grid-column: 4; grid-row: 1; }
  .scanner__header > .scanner__orientation { grid-column: 3; grid-row: 1; margin: 0 8px; }
  .scanner__camera { grid-column: 1; width: 100%; height: min(calc(100dvh - 160px), 300px); aspect-ratio: auto; }
  .scanner__actions { grid-column: 2; }
  .scanner__actions .scanner__orientation { margin-bottom: 0; }
  .scanner__action { font-size: 13px; min-height: 44px; padding: 8px; margin-top: 6px; }
  .scanner__message { margin: 0 0 4px; font-size: 12px; }
  .scanner-debug { grid-column: 1 / -1; }
}
@media (orientation: landscape) and (max-height: 600px) and (max-width: 700px) {
  .scanner__title { font-size: 16px; }
}
</style>
