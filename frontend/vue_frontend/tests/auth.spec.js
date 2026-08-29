import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it } from 'vitest';
import { useAuthStore } from '../src/stores/auth';

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
  });

  it('persists and clears the API key', () => {
    const store = useAuthStore();

    store.setApiKey('token-123');
    expect(store.apiKey).toBe('token-123');
    expect(localStorage.getItem('apiKey')).toBe('token-123');

    store.clearApiKey();
    expect(store.apiKey).toBeNull();
    expect(localStorage.getItem('apiKey')).toBeNull();
  });
});
