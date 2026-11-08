<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { ArrowLeft, LogIn, LogOut, ShieldCheck } from 'lucide-vue-next';
import { browserSession, loginAdmin, logoutAdmin, refreshSession, type SessionUser } from '@/services/userSession';
import { request } from '@/services/http';

const login = ref('');
const password = ref('');
const busy = ref(false);
const checking = ref(true);
const error = ref('');
const admin = ref<SessionUser | null>(null);
async function check() {
  checking.value = true;
  error.value = '';
  admin.value = null;
  try {
    const session = await refreshSession();
    if (session.admin) admin.value = await request<SessionUser>('/admin/me', { cache: 'no-store' });
  } catch (reason) { error.value = reason instanceof Error ? reason.message : 'Не удалось проверить сессию'; }
  finally { checking.value = false; }
}
async function submit() {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  try {
    await loginAdmin(login.value.trim(), password.value);
    password.value = '';
    await check();
  } catch (reason) { error.value = reason instanceof Error ? reason.message : 'Не удалось войти'; }
  finally { busy.value = false; password.value = ''; }
}
async function logout(all = false) {
  busy.value = true;
  error.value = '';
  try { await logoutAdmin(all); admin.value = null; }
  catch (reason) { error.value = reason instanceof Error ? reason.message : 'Не удалось завершить сессию'; }
  finally { busy.value = false; if (!browserSession.value?.admin) admin.value = null; }
}
onMounted(() => { void check(); });
</script>

<template>
  <main class="admin-screen">
    <header><RouterLink :to="{ name: 'preferences' }" aria-label="Назад" title="Назад"><ArrowLeft /></RouterLink><h1>{{ admin ? 'Админ-режим' : 'Вход администратора' }}</h1></header>
    <p v-if="checking" role="status">Проверяем сессию...</p>
    <section v-else-if="admin" class="admin-profile">
      <ShieldCheck :size="28" aria-hidden="true" /><h2>{{ admin.display_name || 'Администратор' }}</h2>
      <RouterLink :to="{ name: 'research-admin' }">Исследования: проверки, эталоны и выгрузка →</RouterLink>
      <RouterLink :to="{ name: 'orchestration' }">Экспериментальный стенд: Photo / Video →</RouterLink>
      <dl><dt>Роль</dt><dd>Администратор</dd><dt>Статус</dt><dd>Вход выполнен</dd></dl>
      <button type="button" :disabled="busy" @click="logout()"><LogOut :size="18" aria-hidden="true" />Выйти</button>
      <button type="button" class="secondary" :disabled="busy" @click="logout(true)">Выйти на всех устройствах</button>
    </section>
    <form v-else @submit.prevent="submit">
      <label for="admin-login">Логин</label><input id="admin-login" v-model="login" autocomplete="username" maxlength="100" required :disabled="busy">
      <label for="admin-password">Пароль</label><input id="admin-password" v-model="password" type="password" autocomplete="current-password" maxlength="256" required :disabled="busy">
      <button type="submit" :disabled="busy"><LogIn :size="18" aria-hidden="true" />{{ busy ? 'Входим...' : 'Войти' }}</button>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </main>
</template>

<style scoped>
.admin-screen { min-height: calc(100dvh - 72px); padding: 24px; color: #17201d; background: #f6f8f7; }
header { display: flex; align-items: center; gap: 12px; margin-bottom: 28px; }
header a { display: grid; place-items: center; width: 40px; height: 40px; flex-shrink: 0; color: inherit; }
h1 { font-size: 20px; line-height: 1.3; overflow-wrap: anywhere; }
h2 { font-size: 18px; overflow-wrap: anywhere; }
form { display: grid; gap: 10px; }
label { font-size: 13px; }
input { min-width: 0; width: 100%; min-height: 46px; padding: 10px; border: 1px solid #bfcec6; border-radius: 8px; background: white; font: inherit; margin-bottom: 12px; }
button { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; min-height: 46px; padding: 10px; border: 0; border-radius: 8px; background: #1b805e; color: white; font: inherit; font-size: 14px; cursor: pointer; }
button:disabled { opacity: .6; cursor: default; }
.secondary { color: #a42e38; background: transparent; border: 1px solid #e4c3c7; }
.admin-profile { display: grid; gap: 16px; }
.admin-profile > svg { color: #1b805e; }
dl { display: grid; grid-template-columns: auto 1fr; gap: 10px 18px; font-size: 13px; padding-block: 18px; border-block: 1px solid #dfe6e3; }
dt { color: #66736e; }
.error { margin-top: 18px; color: #a42e38; font-size: 14px; overflow-wrap: anywhere; }
:is(a, button, input):focus-visible { outline: 2px solid #218060; outline-offset: 3px; }
</style>
