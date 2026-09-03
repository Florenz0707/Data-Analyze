import axios from 'axios';
import { useAuthStore } from '../stores/auth';
import { attachTraceContext, reportClientError } from './errors';

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true,
});

let refreshPromise = null;

const extractAccessToken = (response) => {
  const authorization = response?.headers?.authorization;
  return authorization?.startsWith('Bearer ') ? authorization.slice(7) : null;
};

const clearAuthAndRedirect = () => {
  const authStore = useAuthStore();
  authStore.clearApiKey();
  if (window.location.pathname !== '/login') {
    window.location.href = '/login';
  }
};

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
  async (error) => {
    attachTraceContext(error);
    const originalRequest = error.config;
    const isUnauthorized = error.response?.status === 401;
    const isRefreshRequest = originalRequest?.url?.endsWith('/refresh');
    const isAuthRequest = ['/users/login', '/users/register', '/logout'].some((path) =>
      originalRequest?.url?.endsWith(path),
    );

    if (
      isUnauthorized &&
      originalRequest &&
      !originalRequest._retry &&
      !isRefreshRequest &&
      !isAuthRequest
    ) {
      originalRequest._retry = true;
      try {
        if (!refreshPromise) {
          refreshPromise = apiClient
            .post('/refresh', null, { _skipAuthRefresh: true })
            .then((response) => {
              const token = extractAccessToken(response);
              if (!token) {
                throw new Error('Refresh response did not contain an access token');
              }
              useAuthStore().setApiKey(token);
              return token;
            })
            .finally(() => {
              refreshPromise = null;
            });
        }
        const token = await refreshPromise;
        originalRequest.headers = originalRequest.headers || {};
        originalRequest.headers.Authorization = `Bearer ${token}`;
        return apiClient(originalRequest);
      } catch (refreshError) {
        clearAuthAndRedirect();
        reportClientError(refreshError, {
          source: 'axios.refresh',
          traceId: error.traceId,
          requestId: error.requestId,
        });
        return Promise.reject(refreshError);
      }
    }

    if (isUnauthorized) {
      clearAuthAndRedirect();
    }
    reportClientError(error, { source: 'axios' });
    return Promise.reject(error);
  },
);

export default apiClient;
