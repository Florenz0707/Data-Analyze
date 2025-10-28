import { defineStore } from 'pinia';
import api from './api'; // (NEW) 引入 api

// 辅助函数：获取当前用户的 localStorage key
const getUserKey = (key, apiKey) => {
  if (!apiKey) {
    console.error("尝试在没有 apiKey 的情况下获取用户key");
    return null; 
  }
  return `${key}_${apiKey}`;
};

export const useStore = defineStore('main', {
  state: () => ({
    apiKey: localStorage.getItem('apiKey') || null,
    
    currentSession: null,
    sessions: [],
    messages: {}, 
    
    loading: false,
    error: null,
    isInitialized: false, 
  }),

  actions: {
    // (已修改) 初始化用户专有的 Store, 现在从 API 获取会话
    async initializeUserStore() {
      if (!this.apiKey) {
        this.isInitialized = false;
        return;
      }

      this.isInitialized = false;
      this.setLoading(true);
      try {
        // (NEW) 1. 从 API 获取会话列表
        const response = await api.getSessionList();
        let apiSessions = response.data.sessions || [];

        // (NEW) 2. 如果 API 没有会话, 创建一个默认会话
        if (apiSessions.length === 0) {
          // addSession 现在是 async 并且会调用 API
          await this.addSession('default_session'); 
          // addSession 内部会更新 this.sessions
        } else {
          // 如果 API 有会话, 同步到 state 和 localStorage
          this.sessions = apiSessions;
          localStorage.setItem(getUserKey('sessions', this.apiKey), JSON.stringify(this.sessions));
        }

        // (NEW) 3. 加载并验证 currentSession
        const userCurrentSessionKey = getUserKey('currentSession', this.apiKey);
        let userCurrentSession = localStorage.getItem(userCurrentSessionKey);

        // 检查 localStroage 的 session 是否在 (新的) session 列表中
        if (!userCurrentSession || !this.sessions.includes(userCurrentSession)) {
          userCurrentSession = this.sessions[0]; // 默认
          localStorage.setItem(userCurrentSessionKey, userCurrentSession);
        }

        this.currentSession = userCurrentSession;
        this.messages = {}; // 清空可能残留的上一用户的消息
        this.isInitialized = true;

      } catch (error) {
        this.setError('加载会话列表失败');
        console.error("initializeUserStore 失败:", error);
        // (NEW) 即使失败了, 也设置一个本地的, 避免应用卡死
        this.sessions = JSON.parse(localStorage.getItem(getUserKey('sessions', this.apiKey)) || '["default_session"]');
        this.currentSession = localStorage.getItem(getUserKey('currentSession', this.apiKey)) || this.sessions[0];
        this.isInitialized = true; // 标记为已初始化, 即使是回退状态
      } finally {
        this.setLoading(false);
      }
    },

    setApiKey(key) {
      this.apiKey = key;
      localStorage.setItem('apiKey', key);
      this.initializeUserStore(); // 自动初始化
    },

    clearApiKey() {
      const oldKey = this.apiKey; 
      
      this.apiKey = null;
      this.currentSession = null;
      this.sessions = [];
      this.messages = {};
      this.isInitialized = false;

      localStorage.removeItem('apiKey');
      
      if (oldKey) {
        localStorage.removeItem(getUserKey('sessions', oldKey));
        localStorage.removeItem(getUserKey('currentSession', oldKey));
      }
    },

    // (已修改) 添加新会话, 现在会调用 API
    async addSession(sessionId) {
      if (!this.sessions.includes(sessionId)) {
        try {
          // (NEW) 1. 先通知后端创建
          await api.createSession(sessionId);
          
          // (NEW) 2. 成功后再更新本地状态
          this.sessions.push(sessionId);
          localStorage.setItem(getUserKey('sessions', this.apiKey), JSON.stringify(this.sessions));
        
        } catch (error) {
          this.setError('创建会话失败');
          console.error("addSession 失败:", error);
          return; // (NEW) 失败则不继续
        }
      }
      this.setCurrentSession(sessionId);
    },

    setCurrentSession(sessionId) {
      this.currentSession = sessionId;
      localStorage.setItem(getUserKey('currentSession', this.apiKey), sessionId);
    },

    // (已修改) 删除会话, API 调用移至 Chat.vue, store 只负责本地状态
    removeSession(sessionId) {
      this.sessions = this.sessions.filter(id => id !== sessionId);
      localStorage.setItem(getUserKey('sessions', this.apiKey), JSON.stringify(this.sessions));

      if (sessionId === this.currentSession) {
        const newSession = this.sessions.length > 0 ? this.sessions[0] : null;
        
        if (newSession) {
          this.setCurrentSession(newSession);
        } else {
          // (NEW) 如果没有会话了, 异步创建一个
          this.addSession('default_session');
        }
      }
    },

    addMessage(sessionId, isUser, content) {
      if (!this.messages[sessionId]) {
        this.messages[sessionId] = [];
      }

      this.messages[sessionId].push({
        id: Date.now() + Math.random(), 
        isUser,
        content,
        timestamp: new Date().toLocaleString(), 
      });
    },

    loadHistory(sessionId, historyMessages) {
      if (!Array.isArray(historyMessages)) {
        console.error("loadHistory 接收到的不是一个数组:", historyMessages);
        this.messages[sessionId] = [];
        return;
      }

      this.messages[sessionId] = historyMessages.map((msg, index) => ({
        ...msg,
        id: `${sessionId}_history_${index}`, 
        isUser: msg.isUser,
        content: msg.content,
        timestamp: msg.timestamp || new Date().toLocaleString()
      }));
    },

    clearSessionMessages(sessionId) {
      this.messages[sessionId] = [];
    },

    setLoading(state) {
      this.loading = state;
    },

    setError(message) {
      this.error = message;
      if (this.errorTimer) clearTimeout(this.errorTimer);
      this.errorTimer = setTimeout(() => {
        this.error = null;
      }, 3000);
    }
  }
});
