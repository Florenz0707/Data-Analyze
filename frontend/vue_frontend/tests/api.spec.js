import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as llm from '../src/api/llm';
import * as apiIndex from '../src/api/index';
import * as sessions from '../src/api/sessions';
import * as users from '../src/api/users';
import apiClient from '../src/api/client';

vi.mock('../src/api/client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
    request: vi.fn(),
  },
}));

describe('API modules', () => {
  beforeEach(() => vi.clearAllMocks());

  it('maps user and session operations to the expected API contracts', () => {
    users.login('alice', 'secret');
    users.register('alice', 'secret');
    users.refreshToken();
    users.logout();
    sessions.getSessionList();
    sessions.createSession('session-1');
    sessions.deleteSession('session-1');
    sessions.getHistory('session-1');
    sessions.clearHistory('session-1');

    expect(apiClient.post).toHaveBeenNthCalledWith(1, '/users/login', {
      username: 'alice',
      password: 'secret',
    });
    expect(apiClient.post).toHaveBeenNthCalledWith(2, '/users/register', {
      username: 'alice',
      password: 'secret',
    });
    expect(apiClient.post).toHaveBeenNthCalledWith(3, '/refresh');
    expect(apiClient.request).toHaveBeenCalledWith({
      method: 'DELETE',
      url: '/sessions',
      data: { session_id: 'session-1' },
    });
    expect(apiClient.get).toHaveBeenCalledWith('/sessions/history', {
      params: { session_id: 'session-1' },
    });
    expect(apiIndex.login).toBe(users.login);
    expect(apiIndex.getSessionList).toBe(sessions.getSessionList);
  });

  it('maps LLM and custom-model operations to the expected API contracts', () => {
    llm.chat('session-1', 'hello');
    llm.getProviders();
    llm.getLocalModels();
    llm.selectModel('ollama', 'model-a');
    llm.getCurrentModel();
    llm.getCustomModels();
    llm.addCustomModel({ model_name: 'custom' });
    llm.deleteCustomModel('custom');

    expect(apiClient.post).toHaveBeenCalledWith('/llm/chat', {
      session_id: 'session-1',
      user_input: 'hello',
      use_history: 'auto',
    });
    expect(apiClient.delete).toHaveBeenCalledWith('/llm/extern', {
      data: { model_name: 'custom' },
    });
  });
});
