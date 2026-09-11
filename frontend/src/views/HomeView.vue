<script setup lang="ts">
import {
  ScanLine,
  Barcode,
  FileText,
  ChevronRight
} from 'lucide-vue-next';
</script>

<template>
  <main class="home">
    <header class="home__header">
      <div>
        <p class="home__greeting">Добро пожаловать</p>
        <h1 class="home__title">ToxiCheck</h1>
      </div>

      <span class="home__plan">Free</span>
    </header>

    <RouterLink class="home__scan-button" :to="{ name: 'scan' }">
      <ScanLine :size="28" aria-hidden="true" />

      <span>Сканировать продукт</span>
    </RouterLink>

    <section class="home__methods">
      <h2 class="home__section-title">
        Способ проверки
      </h2>

      <RouterLink class="scan-method" :to="{
        name: 'scan',
        query: { mode: 'barcode' }
      }">
        <span class="scan-method__icon">
          <Barcode :size="24" aria-hidden="true" />
        </span>

        <span class="scan-method__content">
          <strong class="scan-method__title">
            Штрихкод
          </strong>

          <span class="scan-method__description">
            Найти продукт в базе
          </span>
        </span>

        <ChevronRight class="scan-method__arrow" :size="20" aria-hidden="true" />
      </RouterLink>

      <RouterLink class="scan-method" :to="{
        name: 'scan',
        query: { mode: 'text' }
      }">
        <span class="scan-method__icon">
          <FileText :size="24" aria-hidden="true" />
        </span>

        <span class="scan-method__content">
          <strong class="scan-method__title">
            Состав продукта
          </strong>

          <span class="scan-method__description">
            Сфотографировать текст
          </span>
        </span>

        <ChevronRight class="scan-method__arrow" :size="20" aria-hidden="true" />
      </RouterLink>
    </section>
  </main>
</template>

<style scoped>
.home {
  min-height: 100%;
  box-sizing: border-box;
  padding: 24px;
  background-color: #f8faf9;
}

.home__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}

.home__greeting {
  margin: 0 0 4px;
  color: #87938f;
  font-size: 13px;
}

.home__title {
  margin: 0;
  color: #17201d;
  font-size: 24px;
  line-height: 1.2;
}

.home__plan {
  padding: 6px 12px;
  border-radius: 999px;
  color: #25a777;
  background-color: #e9f6f1;
  font-size: 12px;
  font-weight: 600;
}

.home__scan-button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;

  min-height: 56px;
  border-radius: 14px;

  color: #ffffff;
  background-color: #25a777;

  font-size: 16px;
  font-weight: 600;
  text-decoration: none;
}

.home__methods {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 28px;
}

.home__section-title {
  margin: 0 0 4px;
  color: #17201d;
  font-size: 17px;
}

.scan-method {
  display: flex;
  align-items: center;
  gap: 14px;

  padding: 16px;
  border: 1px solid #e1e7e4;
  border-radius: 14px;

  color: inherit;
  background-color: #ffffff;
  text-decoration: none;
}

.scan-method__icon {
  flex-shrink: 0;
  display: grid;
  place-items: center;

  width: 44px;
  height: 44px;
  border-radius: 12px;

  color: #25a777;
  background-color: #e9f6f1;
}

.scan-method__content {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
}

.scan-method__title {
  color: #17201d;
  font-size: 15px;
}

.scan-method__description {
  color: #87938f;
  font-size: 13px;
}

.scan-method__arrow {
  flex-shrink: 0;
  color: #a5afab;
}

.scan-method:active {
  background-color: #f0f7f4;
}
</style>
