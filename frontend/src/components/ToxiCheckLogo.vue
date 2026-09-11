<script setup lang="ts">
type IconProps = {
  colorBg?: string,
  colorShield?: string,
  shieldStroke?: string,
  size?: number
}
const { colorBg = "#51B992", colorShield = "none", shieldStroke = "#FFFFFF", size = 80 } = defineProps<IconProps>();
</script>

<template>
  <svg xmlns="http://www.w3.org/2000/svg" :width="size" :height="size" viewBox="0 0 80 80" role="img"
    aria-label="Логотип ToxiCheck">
    <rect width="80" height="80" rx="16" :fill="colorBg" />

    <path d="M40 23.5
           C43.3 26.4 47.3 28.2 52.5 28.9
           V41.7
           C52.5 49.1 47.5 54.6 40 56.8
           C32.5 54.6 27.5 49.1 27.5 41.7
           V28.9
           C32.7 28.2 36.7 26.4 40 23.5Z" :fill="colorShield" :stroke="shieldStroke" stroke-width="3.5"
      stroke-linecap="round" stroke-linejoin="round" />
  </svg>

</template>

<style scoped></style>
