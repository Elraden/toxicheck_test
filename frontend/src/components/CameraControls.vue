<script setup lang="ts">
import {
  Flashlight,
  FlashlightOff,
  ZoomIn
} from 'lucide-vue-next';

type CameraControlsProps = {
  canZoom: boolean;
  zoom: number;
  zoomMin: number;
  zoomMax: number;
  zoomStep?: number;

  canUseTorch: boolean;
  torchEnabled: boolean;

  disabled?: boolean;
};

withDefaults(
  defineProps<CameraControlsProps>(),

  {
    zoomStep: 0.1,
    disabled: false
  }
);

const emit = defineEmits<{
  zoomChange: [value: number];
  torchToggle: [];
}>();

function handleZoomChange(event: Event): void {
  const input =
    event.currentTarget as HTMLInputElement;

  emit(
    'zoomChange',
    Number(input.value)
  );
}
</script>

<template>
  <div v-if="canZoom || canUseTorch" class="camera-controls">
    <label v-if="canZoom" class="camera-controls__zoom">
      <span class="camera-controls__zoom-label">
        <ZoomIn :size="18" />

        {{ zoom.toFixed(1) }}×
      </span>

      <input class="camera-controls__range" type="range" :value="zoom" :min="zoomMin" :max="zoomMax" :step="zoomStep"
        :disabled="disabled" aria-label="Увеличение камеры" @input="handleZoomChange">
    </label>

    <button v-if="canUseTorch" class="camera-controls__torch" type="button" :disabled="disabled"
      :aria-pressed="torchEnabled" :aria-label="torchEnabled
        ? 'Выключить фонарик'
        : 'Включить фонарик'
        " @click="emit('torchToggle')">
      <FlashlightOff v-if="torchEnabled" :size="22" />

      <Flashlight v-else :size="22" />
    </button>
  </div>
</template>

<style scoped>
.camera-controls {
  position: absolute;
  z-index: 4;
  right: 12px;
  bottom: 12px;
  left: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 14px;
  color: #ffffff;
  background-color: rgb(23 32 29 / 75%);
  backdrop-filter: blur(8px);
}

.camera-controls__zoom {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 10px;
}

.camera-controls__zoom-label {
  display: flex;
  min-width: 58px;
  align-items: center;
  gap: 5px;
  font-size: 14px;
}

.camera-controls__range {
  flex: 1;
  accent-color: #51b992;
}

.camera-controls__torch {
  display: grid;
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  place-items: center;
  border: 0;
  border-radius: 50%;
  color: #ffffff;
  background-color: rgb(255 255 255 / 18%);
  cursor: pointer;
}

.camera-controls__torch[aria-pressed='true'] {
  color: #17201d;
  background-color: #ffffff;
}

.camera-controls__torch:disabled,
.camera-controls__range:disabled {
  cursor: default;
  opacity: 0.5;
}
</style>
