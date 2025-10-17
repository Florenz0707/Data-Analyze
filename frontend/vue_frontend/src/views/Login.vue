<template>
  <div class="login-container">
    <div class="login-card">
      <h1 class="title">DeepSeek-KAI 客户端</h1>
      <p class="subtitle">请登录以继续</p>

      <div v-if="error" class="error-message">{{ error }}</div>

      <form @submit.prevent="handleLogin" class="login-form">
        <div class="form-group">
          <label for="username">用户名</label>
          <input
            type="text"
            id="username"
            v-model="username"
            required
            placeholder="输入用户名"
          />
        </div>

        <div class="form-group">
          <label for="password">密码</label>
          <input
            type="password"
            id="password"
            v-model="password"
            required
            placeholder="输入密码 (默认: secret)"
          />
        </div>

        <button type="submit" class="primary login-button" :disabled="loading">
          <span v-if="loading" class="loading"></span>
          <span v-else>登录</span>
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { useStore } from '../store';
import api from '../api';

const username = ref('');
const password = ref('');
const loading = ref(false);
const error = ref('');

const router = useRouter();
const store = useStore();

const handleLogin = async () => {
  loading.value = true;
  error.value = '';

  try {
    const response = await api.login(username.value, password.value);
    store.setApiKey(response.data.api_key);
    router.push('/');
  } catch (err) {
    error.value = err.response?.data?.error || '登录失败，请检查用户名和密码';
  } finally {
    loading.value = false;
  }
};
</script>

<style scoped>
.login-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  padding: 1rem;
}

.login-card {
  width: 100%;
  max-width: 400px;
  text-align: center;
}

.title {
  color: var(--primary-color);
  margin-bottom: 0.5rem;
  font-size: 2rem;
}

.subtitle {
  color: var(--text-secondary);
  margin-bottom: 2rem;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.form-group {
  text-align: left;
}

.form-group label {
  display: block;
  margin-bottom: 0.5rem;
  font-weight: 500;
}

.login-button {
  width: 100%;
  padding: 0.75rem;
  font-size: 1rem;
  margin-top: 1rem;
}

.loading {
  margin-right: 0.5rem;
  vertical-align: middle;
}
</style>
