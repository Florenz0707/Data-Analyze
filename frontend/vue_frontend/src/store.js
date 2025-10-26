import { defineStore } from 'pinia';

// (新) 辅助函数：获取当前用户的 localStorage key
const getUserKey = (key, apiKey) => {
  if (!apiKey) {
    console.error("尝试在没有 apiKey 的情况下获取用户key");
    return null; // 或者返回一个默认值，但最好是报错
  }
  return `${key}_${apiKey}`;
};

export const useStore = defineStore('main', {
  state: () => ({
    apiKey: localStorage.getItem('apiKey') || null,
    
    // (已修改) 状态不再从 localStorage 初始化，等待 initializeUserStore
    currentSession: null,
    sessions: [],
    messages: {}, 
    
    loading: false,
    error: null,
    isInitialized: false, // (新) 跟踪 store 是否已为当前用户初始化
  }),

  actions: {
    // (新) 初始化用户专有的 Store
    // 必须在 setApiKey 之后或页面加载时（如果 apiKey 已存在）调用
    initializeUserStore() {
      if (!this.apiKey) {
        this.isInitialized = false;
        return;
      }

      const userSessionsKey = getUserKey('sessions', this.apiKey);
      const userCurrentSessionKey = getUserKey('currentSession', this.apiKey);

      const userSessions = JSON.parse(localStorage.getItem(userSessionsKey) || '["default_session"]');
      const userCurrentSession = localStorage.getItem(userCurrentSessionKey) || userSessions[0];

      this.sessions = userSessions;
      this.currentSession = userCurrentSession;
      this.messages = {}; // 清空可能残留的上一用户的消息
      this.isInitialized = true;
    },

    // 保存API Key
    setApiKey(key) {
      this.apiKey = key;
      localStorage.setItem('apiKey', key);
      
      // (新) 设置 key 后，立即初始化该用户的 store
      this.initializeUserStore();
    },

    // 清除API Key（退出登录）
    clearApiKey() {
      const oldKey = this.apiKey; // 获取旧 key 以清除数据
      
      this.apiKey = null;
      this.currentSession = null;
      this.sessions = [];
      this.messages = {};
      this.isInitialized = false;

      localStorage.removeItem('apiKey');
      
      // (新) 清除特定于该用户的数据
      if (oldKey) {
        localStorage.removeItem(getUserKey('sessions', oldKey));
        localStorage.removeItem(getUserKey('currentSession', oldKey));
      }
    },

    // 添加新会话
    addSession(sessionId) {
      if (!this.sessions.includes(sessionId)) {
        this.sessions.push(sessionId);
        // (已修改) 使用用户专有的 key
        localStorage.setItem(getUserKey('sessions', this.apiKey), JSON.stringify(this.sessions));
      }
      this.setCurrentSession(sessionId);
    },

    // 设置当前会话
    setCurrentSession(sessionId) {
      this.currentSession = sessionId;
      // (已修改) 使用用户专有的 key
      localStorage.setItem(getUserKey('currentSession', this.apiKey), sessionId);
    },

    // 删除会话
    removeSession(sessionId) {
      this.sessions = this.sessions.filter(id => id !== sessionId);
      // (已修改) 使用用户专有的 key
      localStorage.setItem(getUserKey('sessions', this.apiKey), JSON.stringify(this.sessions));

      if (sessionId === this.currentSession) {
        const newSession = this.sessions.length > 0 ? this.sessions[0] : 'default_session';
        // (新) 如果 default_session 不在了, 确保它被加回去
        if (this.sessions.length === 0) {
          this.addSession(newSession);
        } else {
          this.setCurrentSession(newSession);
        }
      }
    },

    // 保存消息到状态
    addMessage(sessionId, isUser, content) {
      if (!this.messages[sessionId]) {
        this.messages[sessionId] = [];
      }

      this.messages[sessionId].push({
        id: Date.now() + Math.random(), // 增加一点随机性避免key冲突
        isUser,
        content,
        timestamp: new Date().toLocaleString(), // 转换为可读字符串
      });
    },

    // (已修改) 从历史记录加载消息
    // 这个 action 现在接收一个由 Chat.vue 解析好的消息对象数组
    loadHistory(sessionId, historyMessages) {
      if (!Array.isArray(historyMessages)) {
        console.error("loadHistory 接收到的不是一个数组:", historyMessages);
        this.messages[sessionId] = [];
        return;
      }

      this.messages[sessionId] = historyMessages.map((msg, index) => ({
        ...msg,
        id: `${sessionId}_history_${index}`, // 提供一个稳定的 ID
        isUser: msg.isUser,
        content: msg.content,
        timestamp: msg.timestamp || new Date().toLocaleString()
      }));
    },

    // 清空会话消息
    clearSessionMessages(sessionId) {
      this.messages[sessionId] = [];
    },

    // 设置加载状态
    setLoading(state) {
      this.loading = state;
    },

    // 设置错误信息
    setError(message) {
      this.error = message;
      // 3秒后自动清除错误信息
      if (this.errorTimer) clearTimeout(this.errorTimer);
      this.errorTimer = setTimeout(() => {
        this.error = null;
      }, 3000);
    }
  }
});
