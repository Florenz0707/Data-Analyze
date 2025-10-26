<template>
  <div class="login-container">
    <div class="login-card">
      <h1 class="title">DeepSeek-KAI 客户端</h1>
      <p class="subtitle">{{ isRegisterMode ? '创建新账户' : '请登录以继续' }}</p>

      <div v-if="error" class="error-message">{{ error }}</div>
      <div v-if="successMessage" class="success-message">{{ successMessage }}</div>

      <form @submit.prevent="handleSubmit" class="login-form">
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
            :placeholder="isRegisterMode ? '输入新密码' : '输入密码 (默认: secret)'"
          />
        </div>

        <button type="submit" class="primary login-button" :disabled="loading">
          <span v-if="loading" class="loading"></span>
          <span v-else>{{ isRegisterMode ? '注册' : '登录' }}</span>
        </button>
      </form>

      <div class="toggle-mode">
        <button class="link-button" @click="toggleMode">
          {{ isRegisterMode ? '已有账户？点击登录' : '没有账户？点击注册' }}
        </button>
      </div>
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
const successMessage = ref(''); // 用于显示注册成功消息
const isRegisterMode = ref(false); // 切换登录/注册

const router = useRouter();
const store = useStore();

const toggleMode = () => {
  isRegisterMode.value = !isRegisterMode.value;
  error.value = '';
  successMessage.value = '';
};

const handleSubmit = async () => {
  loading.value = true;
  error.value = '';
  successMessage.value = '';

  try {
    if (isRegisterMode.value) {
      // 注册逻辑
      await api.register(username.value, password.value);
      successMessage.value = '注册成功！请使用您的新账户登录。';
      isRegisterMode.value = false; // 切换回登录模式
      password.value = ''; // 清空密码
    } else {
      // 登录逻辑
      const response = await api.login(username.value, password.value);
      store.setApiKey(response.data.api_key);
      router.push('/');
    }
  } catch (err) {
    error.value = err.response?.data?.error || (isRegisterMode.value ? '注册失败' : '登录失败，请检查用户名和密码');
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

/* 成功消息样式 */
.success-message {
  background-color: #dff0d8;
  color: #3c763d;
  border: 1px solid #d6e9c6;
  padding: 0.75rem;
  border-radius: var(--radius);
  margin-bottom: 1rem;
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

.toggle-mode {
  margin-top: 1.5rem;
}

.link-button {
  background: none;
  border: none;
  color: var(--primary-color);
  cursor: pointer;
  padding: 0;
  font-size: 0.9rem;
}

.link-button:hover {
  text-decoration: underline;
}
</style>
