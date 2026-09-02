import { defineStore } from 'pinia';

export const useAppStore = defineStore('app', {
  state: () => ({
    globalLoading: false,
    isInitializing: false,
    isLoadingHistory: false,
    isSending: false,
    error: null,
    isInitialized: false,
    theme: localStorage.getItem('theme') || 'light',
  }),
  getters: {
    loading: (state) =>
      state.globalLoading || state.isInitializing || state.isLoadingHistory || state.isSending,
  },
  actions: {
    setLoading(state) {
      this.globalLoading = state;
    },
    setTaskLoading(task, state) {
      const taskState = {
        initializing: 'isInitializing',
        history: 'isLoadingHistory',
        sending: 'isSending',
      }[task];
      if (!taskState) throw new Error(`Unknown loading task: ${task}`);
      this[taskState] = state;
    },
    setError(message) {
      this.error = message;
      if (this.errorTimer) clearTimeout(this.errorTimer);
      this.errorTimer = setTimeout(() => {
        this.error = null;
      }, 5000); // Increased timeout for better UX
    },
    clearError() {
      this.error = null;
      if (this.errorTimer) clearTimeout(this.errorTimer);
    },
    setInitialized(state) {
      this.isInitialized = state;
    },
    toggleTheme() {
      this.theme = this.theme === 'light' ? 'dark' : 'light';
      localStorage.setItem('theme', this.theme);
    },
  },
});
