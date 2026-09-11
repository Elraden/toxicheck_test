<script setup lang="ts">
import { Construction } from 'lucide-vue-next';

type UnderDevelopmentNoticeProps = {
  title: string;
};

defineProps<UnderDevelopmentNoticeProps>();
</script>

<template>
  <section class="development-notice">
    <div class="development-notice__icon">
      <Construction aria-hidden="true" />
    </div>

    <div class="development-notice__content">
      <h1 class="development-notice__title">
        {{ title }}
      </h1>

      <p class="development-notice__badge">
        Экран в разработке
      </p>

      <p class="development-notice__text">
        Этот раздел скоро появится в ToxiCheck.
      </p>
    </div>
  </section>
</template>

<style scoped>
.development-notice {
  display: grid;
  gap: 18px;
  align-content: center;
  justify-items: center;
  min-height: calc(100dvh - 72px);
  padding: 32px 24px;
  background:
    radial-gradient(
      circle at 20% 18%,
      rgb(37 167 119 / 12%),
      transparent 32%
    ),
    linear-gradient(180deg, #f8faf9 0%, #eef5f2 100%);
  text-align: center;
}

.development-notice__icon {
  display: grid;
  place-items: center;
  width: 72px;
  height: 72px;
  border: 1px solid #cfe5dc;
  border-radius: 20px;
  color: #25a777;
  background-color: #ffffff;
  box-shadow: 0 14px 36px rgb(23 32 29 / 10%);
}

.development-notice__icon svg {
  width: 34px;
  height: 34px;
  stroke-width: 2;
}

.development-notice__content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  max-width: 320px;
}

.development-notice__title {
  margin: 0;
  color: #17201d;
  font-size: 24px;
  line-height: 1.2;
}

.development-notice__badge {
  margin: 0;
  padding: 8px 14px;
  border: 1px solid #bce2d3;
  border-radius: 999px;
  color: #16875f;
  background-color: #e9f6f1;
  font-size: 13px;
  font-weight: 700;
  line-height: 1;
}

.development-notice__text {
  margin: 0;
  color: #66736e;
  font-size: 14px;
  line-height: 1.5;
}
</style>
