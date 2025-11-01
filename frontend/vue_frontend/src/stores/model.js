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
    customModels: [], // 新增：存储自定义模型列表
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
          this.fetchCustomModels(), // 新增：同时获取自定义模型
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
    async fetchCustomModels() { // 新增：获取自定义模型的 action
      try {
        const response = await api.getCustomModels();
        this.customModels = response.data.models_list || [];
      } catch (error) {
        console.error('Failed to fetch custom models:', error);
        // 可以在 appStore 中设置一个特定的错误
        // const appStore = useAppStore();
        // appStore.setError('Failed to fetch custom models.');
      }
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
    
    // --- 新增 Actions ---

    /**
     * 添加自定义模型
     * @param {object} modelData - { base_url, model_name, api_key, alias }
     */
    async addCustomModel(modelData) {
      const appStore = useAppStore();
      appStore.setLoading(true);
      try {
        await api.addCustomModel(modelData);
        await this.fetchCustomModels(); // 添加成功后刷新列表
        appStore.clearError(); // 清除可能之前的错误
      } catch (error) {
        console.error('Failed to add custom model:', error);
        appStore.setError('Failed to add custom model. Check console for details.');
        throw error; // 抛出错误，以便 UI 层可以捕获它
      } finally {
        appStore.setLoading(false);
      }
    },

    /**
     * 删除自定义模型
     * @param {string} modelName - model_name 或 alias
     */
    async deleteCustomModel(modelName) {
      const appStore = useAppStore();
      appStore.setLoading(true);
      try {
        await api.deleteCustomModel(modelName);
        await this.fetchCustomModels(); // 删除成功后刷新列表
        await this.fetchCurrentModel(); // 检查当前模型是否已被删除
      } catch (error) {
        console.error('Failed to delete custom model:', error);
        appStore.setError('Failed to delete custom model.');
      } finally {
        appStore.setLoading(false);
      }
    },
  },
});

