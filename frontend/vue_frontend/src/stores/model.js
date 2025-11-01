import { defineStore } from 'pinia';
import * as api from '../api';
import { useAppStore } from './app';

export const useModelStore = defineStore('model', {
  state: () => ({
    providers: [],
    localModels: {
      transformers: [],
      ollama: [],
    },
    currentModel: null,
    selectedProvider: null,
    selectedModel: null,
  }),
  actions: {
    async fetchAll() {
      const appStore = useAppStore();
      appStore.setLoading(true);
      try {
        await Promise.all([
          this.fetchProviders(),
          this.fetchLocalModels(),
          this.fetchCurrentModel(),
        ]);
      } catch (error) {
        appStore.setError('Failed to fetch model data.');
      } finally {
        appStore.setLoading(false);
      }
    },
    async fetchProviders() {
      const response = await api.getProviders();
      this.providers = response.data.providers || [];
    },
    async fetchLocalModels() {
      const response = await api.getLocalModels();
      this.localModels = response.data || { transformers: [], ollama: [] };
    },
    async fetchCurrentModel() {
      const response = await api.getCurrentModel();
      this.currentModel = response.data;
      this.selectedProvider = this.currentModel?.provider;
      this.selectedModel = this.currentModel?.model;
    },
    async selectModel(provider, model) {
        const appStore = useAppStore();
        appStore.setLoading(true);
        try {
            const response = await api.selectModel(provider, model);
            this.currentModel = response.data;
        } catch (error) {
            appStore.setError('Failed to select model.');
        } finally {
            appStore.setLoading(false);
        }
    },
  },
});
