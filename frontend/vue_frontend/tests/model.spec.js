import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../src/api';
import { useModelStore } from '../src/stores/model';

vi.mock('../src/api', () => ({
  getProviders: vi.fn(),
  getLocalModels: vi.fn(),
  getCurrentModel: vi.fn(),
  getCustomModels: vi.fn(),
  selectModel: vi.fn(),
  addCustomModel: vi.fn(),
  deleteCustomModel: vi.fn(),
}));

describe('model store', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('loads and updates local and custom model state', async () => {
    api.getProviders.mockResolvedValue({ data: { providers: ['ollama'] } });
    api.getLocalModels.mockResolvedValue({ data: { ollama: ['model-a'] } });
    api.getCurrentModel.mockResolvedValue({ data: { provider: 'ollama', model: 'model-a' } });
    api.getCustomModels.mockResolvedValue({ data: { models_list: ['custom'] } });
    api.selectModel.mockResolvedValue({ data: { provider: 'ollama', model: 'model-b' } });
    api.addCustomModel.mockResolvedValue({});
    api.deleteCustomModel.mockResolvedValue({});

    const store = useModelStore();
    await store.fetchAll();
    await store.selectModel('ollama', 'model-b');
    await store.addCustomModel({ model_name: 'custom' });
    await store.deleteCustomModel('custom');

    expect(store.providers).toEqual(['ollama']);
    expect(store.selectedModel).toBe('model-a');
    expect(store.customModels).toEqual(['custom']);
    expect(api.selectModel).toHaveBeenCalledWith('ollama', 'model-b');
  });

  it('keeps fetchAll resilient when one model endpoint fails', async () => {
    api.getProviders.mockRejectedValue(new Error('offline'));
    api.getLocalModels.mockResolvedValue({ data: {} });
    api.getCurrentModel.mockResolvedValue({ data: {} });
    api.getCustomModels.mockResolvedValue({ data: { models_list: [] } });

    const store = useModelStore();
    await store.fetchAll();

    expect(store.providers).toEqual([]);
  });
});
