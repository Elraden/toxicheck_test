<script setup lang="ts">
import ToxiCheckLogo from '@/components/ToxiCheckLogo.vue';
import { ScanLine, Filter } from 'lucide-vue-next';
</script>

<template>
  <main class="welcome">
    <header class="welcome__header">
      <ToxiCheckLogo color-bg="#E9F6F1" color-shield="#E9F6F1" shield-stroke="#25A777" />

      <h1 class="welcome__title">
        Знай, что покупаешь
      </h1>

      <p class="welcome__description">
        Мгновенная проверка состава продуктов.
        <br />
        Просто наведи камеру на штрихкод.
      </p>
    </header>

    <section class="welcome__features" aria-label="Возможности приложения">
      <article class="welcome-card">
        <div class="welcome-card__icon">
          <ScanLine :size="24" aria-hidden="true" />
        </div>

        <div class="welcome-card__content">
          <h2 class="welcome-card__title">
            Сканируй штрихкод
          </h2>

          <p class="welcome-card__description">
            Результат за 5–10 секунд прямо у полки
          </p>
        </div>
      </article>

      <article class="welcome-card">
        <div class="welcome-card__icon">
          <Filter :size="24" aria-hidden="true" />
        </div>

        <div class="welcome-card__content">
          <h2 class="welcome-card__title">
            Настрой под себя
          </h2>

          <p class="welcome-card__description">
            Персональные фильтры для аллергий и предпочтений
          </p>
        </div>
      </article>
    </section>
    <nav class="welcome__actions" aria-label="Авторизация">
      <RouterLink class="welcome__button welcome__button--primary" to="/home">
        Войти
      </RouterLink>

      <RouterLink class="welcome__button welcome__button--secondary" to="/register">
        Создать аккаунт
      </RouterLink>
    </nav>
  </main>
</template>

<style scoped>
.welcome {
  min-height: 100dvh;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  padding: 48px 24px 32px;
}

.welcome__header {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.welcome__title {
  margin: 24px 0 12px;
  color: #17201d;
  font-size: 28px;
  line-height: 1.2;
}

.welcome__description {
  margin: 0;
  color: #66736e;
  font-size: 15px;
  line-height: 1.5;
}

.welcome__features {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 32px;
}

.welcome-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;
  border-radius: 12px;
  background-color: #e9f6f1;
}

.welcome-card__icon {
  flex-shrink: 0;
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 10px;
  color: #25a777;
  background-color: #ffffff;
}

.welcome-card__content {
  min-width: 0;
}

.welcome-card__title {
  margin: 0 0 4px;
  color: #17201d;
  font-size: 16px;
  line-height: 1.3;
}

.welcome-card__description {
  margin: 0;
  color: #66736e;
  font-size: 14px;
  line-height: 1.4;
}

.welcome__actions {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: auto;
  padding-top: 32px;
}

.welcome__button {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 48px;
  box-sizing: border-box;
  border: 1px solid #25a777;
  border-radius: 10px;
  font-size: 16px;
  font-weight: 600;
  text-decoration: none;
  transition:
    background-color 0.2s ease,
    color 0.2s ease;
}

.welcome__button--primary {
  color: #ffffff;
  background-color: #25a777;
}

.welcome__button--secondary {
  color: #25a777;
  background-color: #ffffff;
}

.welcome__button--primary:hover {
  background-color: #20976b;
}

.welcome__button--secondary:hover {
  background-color: #e9f6f1;
}
</style>
