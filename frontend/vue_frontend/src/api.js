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
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

export default {
  // --- Users ---
  // 登录 (路径更新)
  login(username, password) {
    return api.post('/users/login', { username, password });
  },

  // (新) 注册
  register(username, password) {
    return api.post('/users/register', { username, password });
  },

  // --- Sessions ---
  // 获取历史记录 (路径更新)
  getHistory(sessionId) {
    return api.get('/sessions/history', { params: { session_id: sessionId } });
  },

  // 清空历史记录 (路径更新)
  clearHistory(sessionId) {
    return api.delete('/sessions/history', { params: { session_id: sessionId } });
  },

  // --- LLM ---
  // 发送聊天消息 (路径更新)
  chat(sessionId, userInput) {
    // 根据 OpenAPI 规范, ChatIn 只需要 session_id 和 user_input
    return api.post('/llm/chat', { session_id: sessionId, user_input: userInput });
  },

  // (新) 获取模型提供方
  getProviders() {
    return api.get('/llm/providers');
  },

  // (新) 获取本地模型
  getLocalModels() {
    return api.get('/llm/local_models');
  },

  // (新) 选择模型
  selectModel(provider, model) {
    // 如果 model 为 null 或 undefined, API 请求会自动省略它
    return api.post('/llm/select', { provider, model });
  }
};
