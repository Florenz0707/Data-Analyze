import { defineStore } from 'pinia';

export const useAppStore = defineStore('app', {
  state: () => ({
    loading: false,
    error: null,
    isInitialized: false,
    theme: localStorage.getItem('theme') || 'light',
  }),
  actions: {
    setLoading(state) {
      this.loading = state;
    },
    setError(message) {
      this.error = message;
      if (this.errorTimer) clearTimeout(this.errorTimer);
      this.errorTimer = setTimeout(() => {
        this.error = null;
      }, 5000); // Increased timeout for better UX
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
