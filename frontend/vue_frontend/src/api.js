import axios from 'axios';

const api = axios.create({
  baseURL: '/api', // baseURL 保持 /api
  headers: {
    'Content-Type': 'application/json',
  },
});

// 请求拦截器添加token
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('apiKey');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// 响应拦截器处理错误
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      // 未授权，清除token并跳转到登录页
      localStorage.removeItem('apiKey');
      // (TODO) 可以在这里尝试调用 refreshToken
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

export default {
  // --- Users ---
  login(username, password) {
    return api.post('/users/login', { username, password });
  },

  register(username, password) {
    return api.post('/users/register', { username, password });
  },

  // (NEW) 刷新 Token
  refreshToken() {
    // 假设 refresh token 是 httpOnly cookie, 无需传参
    return api.post('/refresh');
  },

  // --- Sessions ---
  
  // (NEW) 获取会话列表
  getSessionList() {
    return api.get('/sessions');
  },

  // (NEW) 创建会话
  createSession(sessionId) {
    return api.post('/sessions', { session_id: sessionId });
  },

  // (NEW) 删除会话
  deleteSession(sessionId) {
    // DELETE 请求发送 body, 需要使用 request 方法
    return api.request({
      method: 'DELETE',
      url: '/sessions',
      data: { session_id: sessionId }
    });
  },

  getHistory(sessionId) {
    return api.get('/sessions/history', { params: { session_id: sessionId } });
  },

  clearHistory(sessionId) {
    return api.delete('/sessions/history', { params: { session_id: sessionId } });
  },

  // --- LLM ---
  chat(sessionId, userInput) {
    return api.post('/llm/chat', { session_id: sessionId, user_input: userInput });
  },

  getProviders() {
    return api.get('/llm/providers');
  },

  getLocalModels() {
    return api.get('/llm/local_models');
  },

  selectModel(provider, model) {
    return api.post('/llm/select', { provider, model });
  }
};
