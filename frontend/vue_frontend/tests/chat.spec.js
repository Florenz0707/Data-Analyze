import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../src/api';
import { useAuthStore } from '../src/stores/auth';
import { useChatStore } from '../src/stores/chat';

vi.mock('../src/api', () => ({
  chat: vi.fn(),
  streamChat: vi.fn(),
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
    api.streamChat.mockImplementation(async (_sessionId, _text, options) => {
      options.onEvent('done', { type: 'done', reply: 'answer' });
    });
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
    const store = useChatStore();
    await store.initialize();

    await store.sendMessage('hello');

    expect(api.createSession).toHaveBeenCalledOnce();
    expect(api.streamChat).toHaveBeenCalledOnce();
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
    api.streamChat.mockRejectedValue(new Error('offline'));
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

  it('cancels a stream and removes the incomplete assistant message', async () => {
    api.getSessionList.mockResolvedValue({ data: { sessions: ['existing'] } });
    let resolveStream;
    api.streamChat.mockImplementation((_sessionId, _text, options) => {
      options.signal.addEventListener('abort', () => resolveStream());
      return new Promise((resolve) => {
        resolveStream = resolve;
      });
    });
    localStorage.setItem('currentSession_user-token', 'existing');
    const store = useChatStore();
    await store.initialize();

    const sending = store.sendMessage('hello');
    await Promise.resolve();
    store.cancelGeneration();
    await sending;

    expect(store.isStreaming).toBe(false);
    expect(store.messages.existing).toHaveLength(1);
    expect(store.messages.existing[0].isUser).toBe(true);
  });

  it('loads the latest history page and prepends older pages on demand', async () => {
    api.getSessionList.mockResolvedValue({ data: { sessions: ['existing'] } });
    api.getHistory
      .mockResolvedValueOnce({
        data: {
          turns: [{ id: 2, user_input: 'new', response: 'new answer' }],
          has_more_before: true,
          next_before_cursor: 'cursor-2',
        },
      })
      .mockResolvedValueOnce({
        data: {
          turns: [{ id: 1, user_input: 'old', response: 'old answer' }],
          has_more_before: false,
        },
      });
    localStorage.setItem('currentSession_user-token', 'existing');
    const store = useChatStore();

    await store.initialize();
    await store.loadHistory('existing');
    await store.loadOlderHistory('existing');

    expect(api.getHistory).toHaveBeenNthCalledWith(1, 'existing', {
      limit: 100,
      latest: true,
    });
    expect(api.getHistory).toHaveBeenNthCalledWith(2, 'existing', {
      limit: 100,
      before_cursor: 'cursor-2',
    });
    expect(store.messages.existing.map((message) => message.content)).toEqual([
      'old',
      'old answer',
      'new',
      'new answer',
    ]);
    expect(store.hasOlderHistory).toBe(false);
  });

  it('marks a failed message retryable and retries it without duplicating the user message', async () => {
    api.getSessionList.mockResolvedValue({ data: { sessions: ['existing'] } });
    api.streamChat.mockRejectedValueOnce(new Error('offline'));
    api.streamChat.mockImplementationOnce(async (_sessionId, _text, options) => {
      options.onEvent('done', { type: 'done', reply: 'recovered' });
    });
    localStorage.setItem('currentSession_user-token', 'existing');
    const store = useChatStore();
    await store.initialize();

    await store.sendMessage('retry me');
    const userMessage = store.messages.existing[0];
    expect(userMessage.retryable).toBe(true);

    await store.retryMessage(userMessage);

    expect(store.messages.existing).toHaveLength(2);
    expect(store.messages.existing[0].retryable).toBe(false);
    expect(store.messages.existing[1].content).toBe('recovered');
  });
});
