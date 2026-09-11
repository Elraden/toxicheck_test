import {
  computed,
  ref
} from 'vue';

import {
  BrowserMultiFormatOneDReader,
  type IScannerControls
} from '@zxing/browser';

import {
  BarcodeFormat,
  DecodeHintType
} from '@zxing/library';

import { useScannerStore } from '@/stores/scanner';

import type {
  ExtendedCameraCapabilities,
  ExtendedCameraConstraints,
  ExtendedCameraSettings
} from '@/types/camera';

type BarcodeDetectedHandler = (
  barcode: string
) => void | Promise<void>;

const cameraConstraints: MediaStreamConstraints = {
  audio: false,

  video: {
    facingMode: {
      ideal: 'environment'
    },

    width: {
      ideal: 1280
    },

    height: {
      ideal: 720
    },

    frameRate: {
      ideal: 30,
      max: 30
    }
  }
};

const expectedScannerErrors = new Set([
  'NotFoundException',
  'ChecksumException',
  'FormatException'
]);

function createCodeReader() {
  const hints = new Map<
    DecodeHintType,
    BarcodeFormat[]
  >();

  hints.set(
    DecodeHintType.POSSIBLE_FORMATS,
    [
      BarcodeFormat.EAN_13,
      BarcodeFormat.EAN_8,
      BarcodeFormat.UPC_A,
      BarcodeFormat.UPC_E
    ]
  );

  return new BrowserMultiFormatOneDReader(
    hints,

    {
      delayBetweenScanAttempts: 100,
      delayBetweenScanSuccess: 500
    }
  );
}

export function useCameraScanner() {
  const scannerStore = useScannerStore();

  const videoElement =
    ref<HTMLVideoElement | null>(null);

  const cameraCapabilities =
    ref<ExtendedCameraCapabilities | null>(null);

  const codeReader = createCodeReader();

  let mediaStream: MediaStream | null = null;
  let cameraTrack: MediaStreamTrack | null = null;
  let scannerControls: IScannerControls | null = null;

  const canZoom = computed(() => {
    const capability =
      cameraCapabilities.value?.zoom;

    return Boolean(
      capability &&
      capability.max > capability.min
    );
  });

  const canUseTorch = computed(() => {
    return (
      cameraCapabilities.value?.torch === true
    );
  });

  const zoomMin = computed(() => {
    return (
      cameraCapabilities.value?.zoom?.min ?? 1
    );
  });

  const zoomMax = computed(() => {
    return (
      cameraCapabilities.value?.zoom?.max ?? 1
    );
  });

  const zoomStep = computed(() => {
    return (
      cameraCapabilities.value?.zoom?.step ??
      0.1
    );
  });

  async function applyCameraConstraint(
    constraint: ExtendedCameraConstraints
  ): Promise<void> {
    if (!cameraTrack) {
      return;
    }

    await cameraTrack.applyConstraints({
      advanced: [
        constraint as MediaTrackConstraintSet
      ]
    });
  }

  async function enableContinuousFocus():
    Promise<void> {
    const modes =
      cameraCapabilities.value?.focusMode;

    if (
      !cameraTrack ||
      !modes?.includes('continuous')
    ) {
      return;
    }

    try {
      await applyCameraConstraint({
        focusMode: 'continuous'
      });
    } catch (error) {
      // Отсутствие управления фокусом не должно
      // останавливать сам сканер.
      console.warn(
        'Не удалось включить автофокус:',
        error
      );
    }
  }

  async function setZoom(
    requestedZoom: number
  ): Promise<void> {
    const capability =
      cameraCapabilities.value?.zoom;

    if (!cameraTrack || !capability) {
      return;
    }

    const previousZoom = scannerStore.zoom;

    const normalizedZoom = Math.min(
      capability.max,

      Math.max(
        capability.min,
        requestedZoom
      )
    );

    scannerStore.setZoom(normalizedZoom);

    try {
      await applyCameraConstraint({
        zoom: normalizedZoom
      });
    } catch (error) {
      scannerStore.setZoom(previousZoom);

      scannerStore.setScannerMessage(
        'Камера не разрешила изменить увеличение'
      );

      console.error(
        'Ошибка изменения zoom:',
        error
      );
    }
  }

  async function toggleTorch(): Promise<void> {
    if (
      !cameraTrack ||
      !canUseTorch.value
    ) {
      return;
    }

    const nextValue =
      !scannerStore.torchEnabled;

    try {
      await applyCameraConstraint({
        torch: nextValue
      });

      scannerStore.setTorchEnabled(nextValue);
    } catch (error) {
      scannerStore.setTorchEnabled(false);

      scannerStore.setScannerMessage(
        'Не удалось переключить фонарик'
      );

      console.error(
        'Ошибка фонарика:',
        error
      );
    }
  }

  async function configureActiveStream():
    Promise<void> {
    const video = videoElement.value;

    if (!video) {
      throw new Error(
        'Не найден элемент камеры'
      );
    }

    const stream =
      video.srcObject as MediaStream | null;

    if (!stream) {
      throw new Error(
        'Поток камеры не найден'
      );
    }

    const track = stream.getVideoTracks()[0];

    if (!track) {
      throw new Error(
        'Видеодорожка камеры не найдена'
      );
    }

    mediaStream = stream;
    cameraTrack = track;

    const settings =
      track.getSettings() as ExtendedCameraSettings;

    const width =
      settings.width ?? video.videoWidth;

    const height =
      settings.height ?? video.videoHeight;

    scannerStore.setCameraResolution(
      `${width} × ${height}`
    );

    try {
      cameraCapabilities.value =
        track.getCapabilities() as
          ExtendedCameraCapabilities;
    } catch (error) {
      cameraCapabilities.value = null;

      console.warn(
        'Браузер не вернул возможности камеры:',
        error
      );

      return;
    }

    await enableContinuousFocus();

    const zoomCapability =
      cameraCapabilities.value?.zoom;

    if (zoomCapability) {
      const currentZoom =
        settings.zoom ??
        scannerStore.zoom ??
        zoomCapability.min;

      const normalizedZoom = Math.min(
        zoomCapability.max,

        Math.max(
          zoomCapability.min,
          currentZoom
        )
      );

      scannerStore.setZoom(normalizedZoom);
    }
  }

  async function openCamera(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error(
        'Браузер не поддерживает работу с камерой'
      );
    }

    const video = videoElement.value;

    if (!video) {
      throw new Error(
        'Не найден элемент для вывода камеры'
      );
    }

    const stream =
      await navigator.mediaDevices.getUserMedia(
        cameraConstraints
      );

    mediaStream = stream;
    video.srcObject = stream;

    try {
      await video.play();
      await configureActiveStream();
    } catch (error) {
      stream
        .getTracks()
        .forEach((track) => track.stop());

      video.srcObject = null;

      throw error;
    }
  }

  function releaseMediaTracks(): void {
    const streams = new Set<MediaStream>();

    if (mediaStream) {
      streams.add(mediaStream);
    }

    const videoStream =
      videoElement.value
        ?.srcObject as MediaStream | null;

    if (videoStream) {
      streams.add(videoStream);
    }

    streams.forEach((stream) => {
      stream
        .getTracks()
        .forEach((track) => track.stop());
    });

    mediaStream = null;
    cameraTrack = null;
    cameraCapabilities.value = null;

    scannerStore.setTorchEnabled(false);

    if (videoElement.value) {
      videoElement.value.pause();
      videoElement.value.srcObject = null;
    }
  }

  function stopCamera(): void {
    try {
      scannerControls?.stop();
    } catch (error) {
      console.warn(
        'Ошибка остановки ZXing:',
        error
      );
    }

    scannerControls = null;

    releaseMediaTracks();

    scannerStore.setScannerStarted(false);
  }

  async function startBarcodeScanning(
    onDetected: BarcodeDetectedHandler
  ): Promise<void> {
    const video = videoElement.value;

    if (!video) {
      throw new Error(
        'Не найден элемент для вывода камеры'
      );
    }

    let resultHandled = false;
    let attempts = 0;

    scannerStore.setScannerStarted(false);
    scannerStore.setScannerMessage(
      'Запускаем ZXing'
    );

    scannerControls =
      await codeReader.decodeFromConstraints(
        cameraConstraints,

        video,

        (result, error, controls) => {
          if (resultHandled) {
            return;
          }

          attempts += 1;

          // Не обновляем Pinia на каждом кадре,
          // иначе диагностика будет постоянно
          // перерисовывать DOM.
          if (attempts % 5 === 0) {
            scannerStore.setScanAttempts(
              attempts
            );

            scannerStore.setScannerMessage(
              'Ищем штрихкод…'
            );
          }

          if (result) {
            resultHandled = true;

            const detectedCode =
              result.getText();

            scannerStore.setScanAttempts(
              attempts
            );

            scannerStore.setBarcode(
              detectedCode
            );

            scannerStore.setScannerMessage(
              `Найден код: ${detectedCode}`
            );

            navigator.vibrate?.(100);

            controls.stop();
            scannerControls = null;

            releaseMediaTracks();

            scannerStore.setScannerStarted(
              false
            );

            void Promise.resolve()
              .then(() =>
                onDetected(detectedCode)
              )
              .catch((handlerError) => {
                console.error(
                  'Ошибка обработки штрихкода:',
                  handlerError
                );
              });

            return;
          }

          if (
            error &&
            !expectedScannerErrors.has(
              error.name
            )
          ) {
            scannerStore.setScannerMessage(
              `${error.name}: ${error.message}`
            );

            console.error(
              'Ошибка ZXing:',
              error
            );
          }
        }
      );

    if (resultHandled) {
      return;
    }

    await configureActiveStream();

    scannerStore.setScannerStarted(true);
    scannerStore.setScannerMessage(
      'ZXing запущен, ищем штрихкод'
    );
  }

  function captureCurrentFrame(): string {
    const video = videoElement.value;

    if (
      !video ||
      video.videoWidth === 0 ||
      video.videoHeight === 0
    ) {
      throw new Error(
        'Камера ещё не готова'
      );
    }

    const canvas =
      document.createElement('canvas');

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;

    const context =
      canvas.getContext('2d');

    if (!context) {
      throw new Error(
        'Не удалось обработать изображение'
      );
    }

    context.drawImage(
      video,
      0,
      0,
      canvas.width,
      canvas.height
    );

    return canvas.toDataURL(
      'image/jpeg',
      0.9
    );
  }

  return {
    videoElement,

    cameraCapabilities,

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
  };
}
