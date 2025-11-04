import { defineStore } from 'pinia';
import * as api from '../api';
import { useAuthStore } from './auth';
import { useAppStore } from './app';

const getUserKey = (key) => {
  const authStore = useAuthStore();
  if (!authStore.apiKey) return null;
  return `${key}_${authStore.apiKey}`;
};

// 用于表示一个尚未在后端创建的临时新对话
const TEMP_NEW_CHAT_ID = 'temp:new_chat';

export const useChatStore = defineStore('chat', {
  state: () => ({
    currentSession: null,
    sessions: [], // 只存储在后端真实创建的 session IDs
    messages: {}, // { [sessionId]: [message] }
  }),
  getters: {
    // 获取当前会话的显示名称
    sessionDisplayName(state) {
      if (state.currentSession === TEMP_NEW_CHAT_ID) {
        return 'New Chat';
      }
      if (state.currentSession) {
        // ID 格式: session_1678886400000_Hello
        const parts = state.currentSession.split('_');
        // 返回最后一部分 (名称)，或在 "default_session" 这种旧格式下返回
        return parts.length > 2 ? parts.slice(2).join('_') : state.currentSession;
      }
      return 'Chat';
    },
    // 获取用于侧边栏显示的会话列表
    processedSessions(state) {
      return state.sessions.map(id => {
        const parts = id.split('_');
        const displayName = parts.length > 2 ? parts.slice(2).join('_') : id;
        return { id, displayName };
      });
    }
  },
  actions: {
    async initialize() {
      const appStore = useAppStore();
      appStore.setLoading(true);
      try {
        const response = await api.getSessionList();
        const apiSessions = response.data.sessions || [];
        
        this.sessions = apiSessions;
        if (apiSessions.length > 0) {
          localStorage.setItem(getUserKey('sessions'), JSON.stringify(this.sessions));
        }

        const userCurrentSessionKey = getUserKey('currentSession');
        let userCurrentSession = localStorage.getItem(userCurrentSessionKey);

        if (!userCurrentSession || !this.sessions.includes(userCurrentSession)) {
          // 如果本地存储的 session ID 无效或不存在，则切换到 "New Chat" 状态
          this.startNewChat();
        } else {
          this.currentSession = userCurrentSession;
        }
        
        appStore.setInitialized(true);
      } catch (error) {
        appStore.setError('Failed to load session list.');
        // 如果加载失败，也回退到 "New Chat" 状态
        this.sessions = JSON.parse(localStorage.getItem(getUserKey('sessions')) || '[]');
        this.startNewChat();
        appStore.setInitialized(true);
      } finally {
        appStore.setLoading(false);
      }
    },

    // 启动一个新的临时对话
    startNewChat() {
      this.currentSession = TEMP_NEW_CHAT_ID;
      this.messages[TEMP_NEW_CHAT_ID] = [];
    },

    async deleteSession(sessionId) {
      try {
        await api.deleteSession(sessionId);
        this.sessions = this.sessions.filter(id => id !== sessionId);
        localStorage.setItem(getUserKey('sessions'), JSON.stringify(this.sessions));

        if (sessionId === this.currentSession) {
          // 如果删除了当前会话，则切换到 "New Chat"
          this.startNewChat();
        }
      } catch (error) {
        useAppStore().setError('Failed to delete session.');
      }
    },

    setCurrentSession(sessionId) {
      const oldSession = this.currentSession;
      
      // 切换会话
      this.currentSession = sessionId;
      localStorage.setItem(getUserKey('currentSession'), sessionId);

      // 如果从临时会话切换走，则丢弃临时会话的状态
      if (oldSession === TEMP_NEW_CHAT_ID && sessionId !== TEMP_NEW_CHAT_ID) {
        delete this.messages[oldSession];
      }
    },

    addMessage(sessionId, message) {
      if (!this.messages[sessionId]) {
        this.messages[sessionId] = [];
      }
      this.messages[sessionId].push(message);
    },

    // 新的发送消息 action，处理延迟创建
    async sendMessage(text) {
      const appStore = useAppStore();
      let sessionId = this.currentSession;
      const isNewChat = sessionId === TEMP_NEW_CHAT_ID;
      
      const userMessage = { isUser: true, content: text, timestamp: new Date().toLocaleString() };

      // 1. 如果是新对话，先在后端创建
      if (isNewChat) {
        appStore.setLoading(true); // 开始加载
        try {
          // 根据消息内容创建 session ID
          const sessionName = text.substring(0, 7);
          const newId = `session_${Date.now()}_${sessionName}`;

          await api.createSession(newId); // 在后端创建

          // 更新前端 state
          this.sessions.push(newId);
          localStorage.setItem(getUserKey('sessions'), JSON.stringify(this.sessions));
          
          // 转移消息
          this.messages[newId] = [userMessage];
          delete this.messages[TEMP_NEW_CHAT_ID];

          // 切换到新的真实 session
          this.setCurrentSession(newId);
          sessionId = newId; // 更新 sessionId 供后续 API 调用

        } catch (error) {
          appStore.setError('Failed to create session.');
          appStore.setLoading(false);
          return; // 创建失败则停止
        }
        // 注意：创建成功后，Loading 状态保持，等待机器人回复
      } else {
         // 如果是旧对话，直接添加用户消息
        this.addMessage(sessionId, userMessage);
      }

      // 2. 发送消息到 (已存在的) chat API
      appStore.setLoading(true);
      try {
        const response = await api.chat(sessionId, text);
        const botMessage = { isUser: false, content: response.data.reply, timestamp: new Date().toLocaleString() };
        this.addMessage(sessionId, botMessage);
      } catch (error) {
        appStore.setError('Failed to send message.');
      } finally {
        appStore.setLoading(false);
      }
    },

    async loadHistory(sessionId) {
        // 永远不要为临时会话加载历史
        if (sessionId === TEMP_NEW_CHAT_ID) {
            return;
        }
        // 如果消息已定义 (即使是空数组)，则不加载
        if (this.messages[sessionId] !== undefined) {
            return;
        }
        
        const appStore = useAppStore();
        appStore.setLoading(true);
        try {
            const response = await api.getHistory(sessionId);
            
            const turns = response.data.turns || [];
            const newMessages = [];
            const now = new Date().toLocaleString(); 
            console.log('Loading history for session:', sessionId, 'Turns:', turns);

            for (const turn of turns) {
                if (turn.user_input) {
                    newMessages.push({
                        isUser: true,
                        content: turn.user_input,
                        timestamp: now,
                    });
                }
                if (turn.response) {
                    newMessages.push({
                        isUser: false,
                        content: turn.response,
                        timestamp: now,
                    });
                }
            }
            this.messages[sessionId] = newMessages;

        } catch (error) {
            appStore.setError('Failed to load chat history.');
            this.messages[sessionId] = []; // 失败时设置为空数组
        } finally {
            appStore.setLoading(false);
        }
    },

    clearUserChatData() {
        const userSessionsKey = getUserKey('sessions');
        const userCurrentSessionKey = getUserKey('currentSession');
        if (userSessionsKey) localStorage.removeItem(userSessionsKey);
        if (userCurrentSessionKey) localStorage.removeItem(userCurrentSessionKey);
        this.currentSession = null;
        this.sessions = [];
        this.messages = {};
    }
  },
});

