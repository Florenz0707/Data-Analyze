import { defineStore } from 'pinia';
import * as api from '../api';
import { useAuthStore } from './auth';
import { useAppStore } from './app';

const getUserKey = (key) => {
  const authStore = useAuthStore();
  if (!authStore.apiKey) return null;
  return `${key}_${authStore.apiKey}`;
};

export const useChatStore = defineStore('chat', {
  state: () => ({
    currentSession: null,
    sessions: [],
    messages: {}, // { [sessionId]: [message] }
  }),
  actions: {
    async initialize() {
      const appStore = useAppStore();
      appStore.setLoading(true);
      try {
        const response = await api.getSessionList();
        const apiSessions = response.data.sessions || [];

        if (apiSessions.length === 0) {
          await this.createSession('default_session');
        } else {
          this.sessions = apiSessions;
          localStorage.setItem(getUserKey('sessions'), JSON.stringify(this.sessions));
        }

        const userCurrentSessionKey = getUserKey('currentSession');
        let userCurrentSession = localStorage.getItem(userCurrentSessionKey);

        if (!userCurrentSession || !this.sessions.includes(userCurrentSession)) {
          userCurrentSession = this.sessions[0];
          localStorage.setItem(userCurrentSessionKey, userCurrentSession);
        }

        this.currentSession = userCurrentSession;
        this.messages = {};
        appStore.setInitialized(true);
      } catch (error) {
        appStore.setError('Failed to load session list.');
        // Fallback to local data
        this.sessions = JSON.parse(localStorage.getItem(getUserKey('sessions')) || '["default_session"]');
        this.currentSession = localStorage.getItem(getUserKey('currentSession')) || this.sessions[0];
        appStore.setInitialized(true);
      } finally {
        appStore.setLoading(false);
      }
    },

    async createSession(sessionId) {
      if (this.sessions.includes(sessionId)) {
        this.setCurrentSession(sessionId);
        return;
      }
      try {
        await api.createSession(sessionId);
        this.sessions.push(sessionId);
        localStorage.setItem(getUserKey('sessions'), JSON.stringify(this.sessions));
        this.setCurrentSession(sessionId);
      } catch (error) {
        useAppStore().setError('Failed to create session.');
      }
    },

    async deleteSession(sessionId) {
      try {
        await api.deleteSession(sessionId);
        this.sessions = this.sessions.filter(id => id !== sessionId);
        localStorage.setItem(getUserKey('sessions'), JSON.stringify(this.sessions));

        if (sessionId === this.currentSession) {
          const newSession = this.sessions.length > 0 ? this.sessions[0] : null;
          if (newSession) {
            this.setCurrentSession(newSession);
          } else {
            await this.createSession('default_session');
          }
        }
      } catch (error) {
        useAppStore().setError('Failed to delete session.');
      }
    },

    setCurrentSession(sessionId) {
      this.currentSession = sessionId;
      localStorage.setItem(getUserKey('currentSession'), sessionId);
    },

    addMessage(sessionId, message) {
      if (!this.messages[sessionId]) {
        this.messages[sessionId] = [];
      }
      this.messages[sessionId].push(message);
    },

    async loadHistory(sessionId) {
        if (this.messages[sessionId] && this.messages[sessionId].length > 0) {
            return;
        }
        const appStore = useAppStore();
        appStore.setLoading(true);
        try {
            const response = await api.getHistory(sessionId);
            
            // Updated logic to parse the new API response
            const turns = response.data.turns || [];
            const newMessages = [];
            // Mimic old timestamp logic, as API doesn't provide timestamps
            const now = new Date().toLocaleString(); 
            console.log('Loading history for session:', sessionId, 'Turns:', turns);

            for (const turn of turns) {
                // Add user message from the turn
                if (turn.user_input) {
                    newMessages.push({
                        isUser: true,
                        content: turn.user_input,
                        timestamp: now,
                    });
                }
                // Add bot response from the turn
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
