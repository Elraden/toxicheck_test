<script setup lang="ts">
import { answerNames, type Answers } from '@/services/research';
defineProps<{ codes: string[]; modelValue: Answers; disabled?: boolean }>();
const emit = defineEmits<{ 'update:modelValue': [value: Answers] }>();
</script>
<template>
  <fieldset v-for="code in codes" :key="code" class="research-answer" :disabled="disabled">
    <legend>{{ code }}</legend>
    <label v-for="(label, value) in answerNames" :key="value">
      <input type="radio" :name="`answer-${code}`" :value="value" :checked="modelValue[code] === value"
        @change="emit('update:modelValue', { ...modelValue, [code]: value })">{{ label }}
    </label>
  </fieldset>
</template>
