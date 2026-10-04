<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{ severity: string }>();
const status = computed(() => {
  if (['forbidden', 'avoid'].includes(props.severity)) return { tone: 'danger', text: 'Ограничения' };
  if (props.severity === 'attention') return { tone: 'attention', text: 'Внимание' };
  if (props.severity === 'neutral') return { tone: 'neutral', text: 'Нет предупреждений' };
  return { tone: 'unknown', text: 'Оценки нет' };
});
</script>

<template>
  <span class="safety-badge" :class="`safety-badge--${status.tone}`">{{ status.text }}</span>
</template>

<style scoped>
.safety-badge { display: inline-block; width: fit-content; max-width: 100%; padding: 5px 8px; border-radius: 6px; font-size: 11px; font-weight: 600; line-height: 1.4; overflow-wrap: anywhere; }
.safety-badge--unknown { color: #53616a; background: #edf0f2; }
.safety-badge--neutral { color: #276b55; background: #e9f5ef; }
.safety-badge--attention { color: #875607; background: #fff1d6; }
.safety-badge--danger { color: #b03039; background: #ffe9e8; }
</style>
