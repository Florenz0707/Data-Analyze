import { defineStore } from 'pinia';
import * as api from '../api';
import { getApiErrorMessage } from '../api/errors';
import { useAuthStore } from './auth';
import { useAppStore } from './app';

const getUserKey = (key) => {
  const authStore = useAuthStore();
  if (!authStore.apiKey) return null;
  return `${key}_${authStore.apiKey}`;
};

// 用于表示一个尚未在后端创建的临时新对话
const TEMP_NEW_CHAT_ID = 'temp:new_chat';

const createMessageId = () => {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `message-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const HISTORY_PAGE_SIZE = 100;

const messagesFromTurns = (turns) => {
  const now = new Date().toLocaleString();
  const result = [];
  for (const turn of turns) {
    const turnId = turn.message_id || turn.id;
    if (turn.user_input) {
      result.push({
        id: `${turnId}:user`,
        isUser: true,
        content: turn.user_input,
        timestamp: now,
      });
    }
    if (turn.response) {
      result.push({
        id: `${turnId}:assistant`,
        isUser: false,
        content: turn.response,
        timestamp: now,
      });
    }
  }
  return result;
};

export const useChatStore = defineStore('chat', {
  state: () => ({
    currentSession: null,
    sessions: [], // 只存储在后端真实创建的 session IDs
    messages: {}, // { [sessionId]: [message] }
    historyMeta: {},
    isStreaming: false,
    activeAbortController: null,
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
      return state.sessions.map((id) => {
        const parts = id.split('_');
        const displayName = parts.length > 2 ? parts.slice(2).join('_') : id;
        return { id, displayName };
      });
    },
    hasOlderHistory(state) {
      return Boolean(state.historyMeta[state.currentSession]?.has_more_before);
    },
  },
  actions: {
    async initialize() {
      const appStore = useAppStore();
      appStore.setTaskLoading('initializing', true);
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
        appStore.setError(getApiErrorMessage(error, 'Failed to load session list.'));
        // 如果加载失败，也回退到 "New Chat" 状态
        this.sessions = JSON.parse(localStorage.getItem(getUserKey('sessions')) || '[]');
        this.startNewChat();
        appStore.setInitialized(true);
      } finally {
        appStore.setTaskLoading('initializing', false);
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
        this.sessions = this.sessions.filter((id) => id !== sessionId);
        localStorage.setItem(getUserKey('sessions'), JSON.stringify(this.sessions));

        if (sessionId === this.currentSession) {
          // 如果删除了当前会话，则切换到 "New Chat"
          this.startNewChat();
        }
        delete this.historyMeta[sessionId];
      } catch (error) {
        useAppStore().setError(getApiErrorMessage(error, 'Failed to delete session.'));
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
    async sendMessage(text, { retryMessage = null, messageId: requestedMessageId = null } = {}) {
      const appStore = useAppStore();
      let sessionId = this.currentSession;
      const isNewChat = sessionId === TEMP_NEW_CHAT_ID;
      const messageId = requestedMessageId || createMessageId();

      const userMessage = retryMessage || {
        id: messageId,
        isUser: true,
        content: text,
        timestamp: new Date().toLocaleString(),
      };
      userMessage.retryable = false;

      // 1. 如果是新对话，先在后端创建
      if (isNewChat) {
        appStore.setTaskLoading('sending', true);
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
          appStore.setError(getApiErrorMessage(error, 'Failed to create session.'));
          appStore.setTaskLoading('sending', false);
          return; // 创建失败则停止
        }
        // 注意：创建成功后，Loading 状态保持，等待机器人回复
      } else {
        // 如果是旧对话，直接添加用户消息
        if (!retryMessage) this.addMessage(sessionId, userMessage);
      }

      // 2. 发送消息到 (已存在的) chat API
      appStore.setTaskLoading('sending', true);
      const botMessage = {
        id: `${messageId}:assistant`,
        isUser: false,
        content: '',
        timestamp: new Date().toLocaleString(),
        streaming: true,
      };
      this.addMessage(sessionId, botMessage);
      const botMessages = this.messages[sessionId];
      const updateMessage = (messageId, updates) => {
        const message = botMessages.find((item) => item.id === messageId);
        if (message) Object.assign(message, updates);
      };
      const removeIncompleteBotMessage = () => {
        const index = botMessages.findIndex((message) => message.id === botMessage.id);
        if (index >= 0) botMessages.splice(index, 1);
      };
      const controller = new AbortController();
      this.activeAbortController = controller;
      this.isStreaming = true;
      let receivedDone = false;
      let pendingDelta = '';
      let renderTimer = null;
      const flushPendingDelta = () => {
        renderTimer = null;
        if (pendingDelta) {
          updateMessage(botMessage.id, {
            content: `${botMessages.find((message) => message.id === botMessage.id)?.content || ''}${pendingDelta}`,
          });
          pendingDelta = '';
        }
      };
      const scheduleDeltaFlush = () => {
        if (renderTimer !== null) return;
        if (typeof globalThis.requestAnimationFrame === 'function') {
          renderTimer = globalThis.requestAnimationFrame(flushPendingDelta);
        } else {
          renderTimer = globalThis.setTimeout(flushPendingDelta, 0);
        }
      };
      try {
        await api.streamChat(sessionId, text, {
          messageId,
          signal: controller.signal,
          onEvent: (event, data) => {
            if (event === 'delta' || data?.type === 'delta') {
              pendingDelta += data?.text || '';
              scheduleDeltaFlush();
            } else if (event === 'done' || data?.type === 'done') {
              flushPendingDelta();
              const currentContent =
                botMessages.find((message) => message.id === botMessage.id)?.content || '';
              updateMessage(botMessage.id, {
                content: data?.reply || currentContent,
                streaming: false,
              });
              receivedDone = true;
            } else if (event === 'error' || data?.type === 'error') {
              const error = new Error(data?.error || 'Streaming generation failed');
              error.response = { data, status: data?.status || 503 };
              throw error;
            }
          },
        });
        if (!receivedDone) throw new Error('Streaming response ended before completion');
      } catch (error) {
        removeIncompleteBotMessage();
        if (error?.name !== 'AbortError') {
          const userMessages = this.messages[sessionId] || [];
          const currentUserMessage = userMessages.find((message) => message.id === userMessage.id);
          if (currentUserMessage) currentUserMessage.retryable = true;
          appStore.setError(getApiErrorMessage(error, 'Failed to send message.'));
        }
      } finally {
        if (this.activeAbortController === controller) this.activeAbortController = null;
        this.isStreaming = false;
        appStore.setTaskLoading('sending', false);
      }
    },

    cancelGeneration() {
      if (this.activeAbortController) this.activeAbortController.abort();
    },

    async retryMessage(message) {
      if (!message?.isUser || !message.retryable || this.isStreaming) return;
      await this.sendMessage(message.content, {
        retryMessage: message,
        messageId: message.id,
      });
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
      appStore.setTaskLoading('history', true);
      try {
        const response = await api.getHistory(sessionId, {
          limit: HISTORY_PAGE_SIZE,
          latest: true,
        });
        this.messages[sessionId] = messagesFromTurns(response.data.turns || []);
        this.historyMeta[sessionId] = response.data;
      } catch (error) {
        appStore.setError(getApiErrorMessage(error, 'Failed to load chat history.'));
        this.messages[sessionId] = []; // 失败时设置为空数组
      } finally {
        appStore.setTaskLoading('history', false);
      }
    },

    async loadOlderHistory(sessionId = this.currentSession) {
      const meta = this.historyMeta[sessionId];
      if (
        !sessionId ||
        sessionId === TEMP_NEW_CHAT_ID ||
        !meta?.has_more_before ||
        !meta.next_before_cursor
      ) {
        return false;
      }
      const appStore = useAppStore();
      appStore.setTaskLoading('history', true);
      try {
        const response = await api.getHistory(sessionId, {
          limit: HISTORY_PAGE_SIZE,
          before_cursor: meta.next_before_cursor,
        });
        const older = messagesFromTurns(response.data.turns || []);
        const knownIds = new Set((this.messages[sessionId] || []).map((message) => message.id));
        this.messages[sessionId] = [
          ...older.filter((message) => !knownIds.has(message.id)),
          ...(this.messages[sessionId] || []),
        ];
        this.historyMeta[sessionId] = response.data;
        return true;
      } catch (error) {
        appStore.setError(getApiErrorMessage(error, 'Failed to load older chat history.'));
        return false;
      } finally {
        appStore.setTaskLoading('history', false);
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
      this.historyMeta = {};
    },
  },
});
