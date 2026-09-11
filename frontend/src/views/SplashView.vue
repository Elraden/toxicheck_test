<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue';
import { useRouter } from 'vue-router';
import BaseSpinner from '@/components/ui/BaseSpinner.vue';
import ToxiCheckLogo from '@/components/ToxiCheckLogo.vue';


const router = useRouter();

let redirectTimer: number | undefined;

onMounted(() => {
  redirectTimer = window.setTimeout(() => {
    router.replace('/welcome');
  }, 3000);
})

onBeforeUnmount(() => {
  if (redirectTimer !== undefined) {
    window.clearTimeout(redirectTimer);
  }
});
</script>

<template>
  <div class="splash">
    <div class="splash__info">
      <ToxiCheckLogo />
      <h2 class="splash__title">ToxiCheck</h2>
      <p>Проверяй состав за секунды</p>
    </div>
    <BaseSpinner :size="28" :thickness="5" />
  </div>
</template>

<style scoped>
.splash {
  background-color: #25A777;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 20px;
  height: 100dvh;
  text-align: center;
}

.splash__title {
  color: #FFFFFF;
  font-size: 32px;
  margin: 0;
}

.splash__info {
  display: flex;
  flex-direction: column;
  gap: 16px;
  align-items: center;
  color: #BEE5D1;
  font-size: 16px;
}

.splash__link {
  color: #77CAA5;
  font-size: 14px;
  text-decoration: none;
}
</style>
