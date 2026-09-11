<script setup lang="ts">
type SpinnerProps = {
  size?: number;
  color?: string;
  trackColor?: string;
  thickness?: number;
  label?: string;
};

withDefaults(defineProps<SpinnerProps>(), {
  size: 28,
  color: '#FFFFFF',
  trackColor: 'rgba(255, 255, 255, 0.3)',
  thickness: 5,
  label: 'Загрузка'
});
</script>

<template>
  <svg class="base-spinner" :width="size" :height="size" viewBox="0 0 50 50" role="status" :aria-label="label">
    <circle class="base-spinner__track" cx="25" cy="25" r="20" :stroke="trackColor" :stroke-width="thickness" />

    <circle class="base-spinner__indicator" cx="25" cy="25" r="20" :stroke="color" :stroke-width="thickness"
      stroke-linecap="round" />
  </svg>
</template>

<style scoped>
.base-spinner {
  display: block;
  animation: spinner-rotation 0.8s linear infinite;
}

.base-spinner__track,
.base-spinner__indicator {
  fill: none;
}

.base-spinner__indicator {
  stroke-dasharray: 80 126;
}

@keyframes spinner-rotation {
  to {
    transform: rotate(360deg);
  }
}
</style>
