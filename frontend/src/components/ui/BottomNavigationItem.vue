<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router';


type NavigationItemProps = {
  label: string;
  to: RouteLocationRaw;
  variant?: 'default' | 'accent';
};

const { label, to, variant = 'default' } = defineProps<NavigationItemProps>();


</script>

<template>
  <RouterLink :to="to" class="navigation-item" :class="`navigation-item--${variant}`"
    active-class="navigation-item--active">

    <span class="navigation-item__icon" aria-hidden="true">
      <slot name="icon" />
    </span>
    <span class="navigation-item__label">
      {{ label }}
    </span>
  </RouterLink>

</template>

<style scoped>
.navigation-item {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-width: 0;
  padding: 8px 4px;
  color: #98a5a0;
  font-size: 11px;
  line-height: 1;
  text-decoration: none;
  transition: color 0.2s ease;
}

.navigation-item__icon {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
}

.navigation-item__icon :deep(svg) {
  width: 22px;
  height: 22px;
  stroke-width: 2;
}

.navigation-item__label {
  white-space: nowrap;
}

.navigation-item--active {
  color: #25a777;
}

.navigation-item--accent {
  color: #25a777;
}

.navigation-item--accent .navigation-item__icon {
  width: 52px;
  height: 52px;
  margin-top: -28px;
  border-radius: 50%;
  color: #ffffff;
  background-color: #25a777;
  box-shadow: 0 4px 12px rgba(37, 167, 119, 0.3);
}

.navigation-item--accent .navigation-item__icon :deep(svg) {
  width: 26px;
  height: 26px;
}

.navigation-item:focus-visible {
  border-radius: 8px;
  outline: 2px solid #25a777;
  outline-offset: 2px;
}
</style>
