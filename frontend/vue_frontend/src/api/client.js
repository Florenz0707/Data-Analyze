import axios from 'axios';
import { useAuthStore } from '../stores/auth';

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.request.use(
  (config) => {
    const authStore = useAuthStore();
    if (authStore.apiKey) {
      config.headers.Authorization = `Bearer ${authStore.apiKey}`;
    }
    return config;
  },
  (error) => Promise.reject(error),
);

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      const authStore = useAuthStore();
      authStore.clearApiKey();
      window.location.href = '/login';
    }
    return Promise.reject(error);
  },
);

export default apiClient;
