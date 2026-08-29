import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../src/api';
import { useAuthStore } from '../src/stores/auth';
import { useChatStore } from '../src/stores/chat';

vi.mock('../src/api', () => ({
  chat: vi.fn(),
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  getHistory: vi.fn(),
  getSessionList: vi.fn(),
}));

describe('chat store', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    const authStore = useAuthStore();
    authStore.setApiKey('user-token');
    vi.clearAllMocks();
  });

  it('initializes from the API and falls back to a temporary chat', async () => {
    api.getSessionList.mockResolvedValue({ data: { sessions: [] } });
    const store = useChatStore();

    await store.initialize();

    expect(store.currentSession).toBe('temp:new_chat');
    expect(store.sessions).toEqual([]);
    expect(store.messages['temp:new_chat']).toEqual([]);
  });

  it('rolls back a temporary chat when session creation fails', async () => {
    api.getSessionList.mockResolvedValue({ data: { sessions: [] } });
    api.createSession.mockRejectedValue(new Error('offline'));
    const store = useChatStore();
    await store.initialize();

    await store.sendMessage('hello');

    expect(store.currentSession).toBe('temp:new_chat');
    expect(store.sessions).toEqual([]);
    expect(api.chat).not.toHaveBeenCalled();
  });

  it('creates a session before sending the first message', async () => {
    api.getSessionList.mockResolvedValue({ data: { sessions: [] } });
    api.createSession.mockResolvedValue({ data: { session_id: 'created' } });
    api.chat.mockResolvedValue({ data: { reply: 'answer' } });
    const store = useChatStore();
    await store.initialize();

    await store.sendMessage('hello');

    expect(api.createSession).toHaveBeenCalledOnce();
    expect(api.chat).toHaveBeenCalledOnce();
    expect(store.currentSession).toMatch(/^session_\d+_hello$/);
    expect(store.messages[store.currentSession]).toHaveLength(2);
  });

  it('loads existing history and derives stable session display data', async () => {
    api.getSessionList.mockResolvedValue({ data: { sessions: ['session_1_Project_x'] } });
    api.getHistory.mockResolvedValue({
      data: {
        turns: [
          { user_input: 'question', response: 'answer' },
          { user_input: 'only question', response: '' },
          { user_input: '', response: 'only answer' },
        ],
      },
    });
    localStorage.setItem('currentSession_user-token', 'session_1_Project_x');
    const store = useChatStore();

    await store.initialize();
    await store.loadHistory('session_1_Project_x');
    await store.loadHistory('temp:new_chat');

    expect(store.sessionDisplayName).toBe('Project_x');
    expect(store.processedSessions).toEqual([
      { id: 'session_1_Project_x', displayName: 'Project_x' },
    ]);
    expect(store.messages['session_1_Project_x']).toHaveLength(4);
    expect(api.getHistory).toHaveBeenCalledOnce();
  });

  it('handles existing-session send, history failure, deletion, and logout cleanup', async () => {
    api.getSessionList.mockResolvedValue({ data: { sessions: ['existing'] } });
    api.getHistory.mockRejectedValue(new Error('offline'));
    api.deleteSession.mockResolvedValue({});
    api.chat.mockRejectedValue(new Error('offline'));
    localStorage.setItem('currentSession_user-token', 'existing');
    const store = useChatStore();

    await store.initialize();
    await store.loadHistory('existing');
    await store.sendMessage('hello');
    await store.deleteSession('existing');
    store.setCurrentSession('temp:new_chat');
    store.addMessage('temp:new_chat', { content: 'temporary' });
    store.clearUserChatData();

    expect(store.currentSession).toBeNull();
    expect(store.sessions).toEqual([]);
    expect(store.messages).toEqual({});
    expect(localStorage.getItem('sessions_user-token')).toBeNull();
  });
});
