import { defineStore } from 'pinia';

export const useAuthStore = defineStore('auth', {
  state: () => ({
    apiKey: localStorage.getItem('apiKey') || null,
  }),
  actions: {
    setApiKey(key) {
      this.apiKey = key;
      localStorage.setItem('apiKey', key);
    },
    clearApiKey() {
      const oldKey = this.apiKey;
      this.apiKey = null;
      localStorage.removeItem('apiKey');
      // The removal of user-specific data will be handled by the chat store
    },
  },
});
