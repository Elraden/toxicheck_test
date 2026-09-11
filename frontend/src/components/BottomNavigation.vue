<script setup lang="ts">
import {
  House,
  Clock3,
  ScanLine,
  ChartColumn,
  Settings
} from 'lucide-vue-next';

import BottomNavigationItem from './ui/BottomNavigationItem.vue';
</script>

<template>
  <nav class="bottom-navigation" aria-label="Основная навигация">
    <BottomNavigationItem label="Главная" :to="{ name: 'home' }">
      <template #icon>
        <House />
      </template>
    </BottomNavigationItem>

    <BottomNavigationItem label="История" :to="{ name: 'history' }">
      <template #icon>
        <Clock3 />
      </template>
    </BottomNavigationItem>

    <BottomNavigationItem label="Скан" :to="{ name: 'scan' }" variant="accent">
      <template #icon>
        <ScanLine />
      </template>
    </BottomNavigationItem>

    <BottomNavigationItem label="Сравнить" :to="{ name: 'compare' }">
      <template #icon>
        <ChartColumn />
      </template>
    </BottomNavigationItem>

    <BottomNavigationItem label="Профиль" :to="{ name: 'preferences' }">
      <template #icon>
        <Settings />
      </template>
    </BottomNavigationItem>
  </nav>
</template>

<style scoped>
.bottom-navigation {
  max-width: 480px;
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 100;

  display: flex;
  align-items: center;

  box-sizing: border-box;
  min-height: calc(72px + env(safe-area-inset-bottom));
  padding: 8px 12px env(safe-area-inset-bottom);

  background-color: #ffffff;
  border-top: 1px solid #dfe6e3;
}
</style>
