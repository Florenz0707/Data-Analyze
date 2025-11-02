<template>
  <div class="login-view">
    <n-card class="login-card" :bordered="false">
      <!-- Simple Gemini-like title -->
      <n-h1 class="title">
        <span class="gemini-title">Hello</span>
      </n-h1>
      <n-p class="subtitle">{{ isRegisterMode ? 'Create a new account' : 'Sign in to continue' }}</n-p>

      <n-form ref="formRef" :model="formValue" @submit.prevent="handleSubmit">
        <n-form-item-row label="Username" path="username">
          <n-input v-model:value="formValue.username" placeholder="Enter your username" size="large" :round="true" />
        </n-form-item-row>
        <n-form-item-row label="Password" path="password">
          <n-input
            type="password"
            show-password-on="click"
            v-model:value="formValue.password"
            placeholder="Enter your password"
            size="large"
            :round="true"
          />
        </n-form-item-row>

        <n-button block attr-type="submit" :loading="appStore.loading" :disabled="appStore.loading" :round="true" size="large" class="login-button">
          {{ isRegisterMode ? 'Register' : 'Login' }}
        </n-button>
      </n-form>

      <n-p class="toggle-mode">
        <n-button text @click="toggleMode">
          {{ isRegisterMode ? 'Already have an account? Sign in' : "Don't have an account? Sign up" }}
        </n-button>
      </n-p>
    </n-card>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { NCard, NForm, NFormItemRow, NInput, NButton, NH1, NP, useMessage } from 'naive-ui';
import { useAuthStore } from '../stores/auth';
import { useAppStore } from '../stores/app';
import * as api from '../api';

const router = useRouter();
const authStore = useAuthStore();
const appStore = useAppStore();
const message = useMessage();

const isRegisterMode = ref(false);
const formRef = ref(null);
const formValue = ref({
  username: '',
  password: '',
});

const toggleMode = () => {
  isRegisterMode.value = !isRegisterMode.value;
  formValue.value.password = '';
};

const handleSubmit = async () => {
  appStore.setLoading(true);
  try {
    if (isRegisterMode.value) {
      await api.register(formValue.value.username, formValue.value.password);
      message.success('Registration successful! Please log in.');
      isRegisterMode.value = false;
      formValue.value.password = '';
    } else {
      const response = await api.login(formValue.value.username, formValue.value.password);
      const authHeader = response.headers.authorization;
      if (authHeader && authHeader.startsWith('Bearer ')) {
        const token = authHeader.split(' ')[1];
        authStore.setApiKey(token);
        router.push('/');
      } else {
        throw new Error('Invalid credentials or token not received.');
      }
    }
  } catch (err) {
    const errorMessage = err.response?.data?.error || err.message || 'An error occurred.';
    message.error(errorMessage);
  } finally {
    appStore.setLoading(false);
  }
};
</script>

<style scoped>
.login-view {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background-color: #f8fafd; /* Light background */
}
.login-card {
  width: 100%;
  max-width: 400px;
  background-color: #ffffff;
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
}
.title {
  text-align: center;
  margin-bottom: 8px;
  font-weight: 600;
}
/* Style title like Gemini */
.gemini-title {
    background: -webkit-linear-gradient(135deg, #4285f4, #9b59b6, #e94235, #fbbc05);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-size: 2.5rem;
}
.subtitle {
  text-align: center;
  color: #5f6368; /* Darker gray for readability */
  margin-bottom: 24px;
}
.login-button {
  margin-top: 16px;
  /* Bright gradient for login button */
  background: linear-gradient(135deg, #4285f4, #9b59b6);
  color: white;
  font-weight: 500;
  border: none;
}
.login-button:hover {
  background: linear-gradient(135deg, #3a75d9, #8a4eae);
}
.login-button.n-button--disabled {
  background: #f0f0f0;
  color: #aaa;
  opacity: 1;
}
.toggle-mode {
  text-align: center;
  margin-top: 24px;
}
</style>
