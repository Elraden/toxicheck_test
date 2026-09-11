import { computed, ref, shallowRef } from 'vue';
import { defineStore } from 'pinia';

import type {
  ScanMode,
  ScannerStatus
} from '@/types/camera';

export const useScannerStore = defineStore(
  'scanner',

  () => {
    const mode = ref<ScanMode>('barcode');
    const status = ref<ScannerStatus>('idle');
    const message = ref('');

    const barcode = ref('');
    const productJson = shallowRef<unknown | null>(null);
    const capturedImage = ref<string | null>(null);

    const scannerStarted = ref(false);
    const scanAttempts = ref(0);
    const scannerMessage = ref(
      'Сканер ещё не запущен'
    );

    const cameraResolution = ref('Не определено');

    const zoom = ref(1);
    const torchEnabled = ref(false);

    const formattedJson = computed(() => {
      if (productJson.value === null) {
        return '';
      }

      return JSON.stringify(
        productJson.value,
        null,
        2
      );
    });

    function setMode(value: ScanMode): void {
      mode.value = value;
    }

    function setStatus(
      value: ScannerStatus,
      text: string
    ): void {
      status.value = value;
      message.value = text;
    }

    function setBarcode(value: string): void {
      barcode.value = value;
    }

    function setProductJson(value: unknown): void {
      productJson.value = value;
    }

    function setCapturedImage(
      value: string | null
    ): void {
      capturedImage.value = value;
    }

    function setScannerStarted(
      value: boolean
    ): void {
      scannerStarted.value = value;
    }

    function setScanAttempts(value: number): void {
      scanAttempts.value = value;
    }

    function setScannerMessage(value: string): void {
      scannerMessage.value = value;
    }

    function setCameraResolution(
      value: string
    ): void {
      cameraResolution.value = value;
    }

    function setZoom(value: number): void {
      zoom.value = value;
    }

    function setTorchEnabled(
      value: boolean
    ): void {
      torchEnabled.value = value;
    }

    function resetForNewScan(): void {
      status.value = 'idle';
      message.value = '';

      barcode.value = '';
      productJson.value = null;
      capturedImage.value = null;

      scannerStarted.value = false;
      scanAttempts.value = 0;
      scannerMessage.value =
        'Сканер ещё не запущен';

      cameraResolution.value = 'Не определено';
      torchEnabled.value = false;
    }

    return {
      mode,
      status,
      message,

      barcode,
      productJson,
      capturedImage,

      scannerStarted,
      scanAttempts,
      scannerMessage,
      cameraResolution,

      zoom,
      torchEnabled,

      formattedJson,

      setMode,
      setStatus,
      setBarcode,
      setProductJson,
      setCapturedImage,

      setScannerStarted,
      setScanAttempts,
      setScannerMessage,
      setCameraResolution,

      setZoom,
      setTorchEnabled,

      resetForNewScan
    };
  }
);
