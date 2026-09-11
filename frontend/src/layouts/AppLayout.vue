<script setup lang="ts">
import BottomNavigation from '@/components/BottomNavigation.vue';
</script>

<template>
  <div class="app-layout">
    <main class="app-layout__content">
      <RouterView />
    </main>

    <BottomNavigation />

  </div>
</template>

<style scoped>
.app-layout {
  min-height: 100dvh;
  background-color: #f6f8f7;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.app-layout__content {
  padding-bottom: calc(72px + env(safe-area-inset-bottom));
}
</style>
