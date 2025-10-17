import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
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
  // 登录
  login(username, password) {
    return api.post('/login', { username, password });
  },

  // 发送聊天消息
  chat(sessionId, userInput) {
    return api.post('/chat', { session_id: sessionId, user_input: userInput });
  },

  // 获取历史记录
  getHistory(sessionId) {
    return api.get('/history', { params: { session_id: sessionId } });
  },

  // 清空历史记录
  clearHistory(sessionId) {
    return api.delete('/history', { params: { session_id: sessionId } });
  }
};
