import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useAuthStore } from '../src/stores/auth';
import { streamChat } from '../src/api/llm';

vi.mock('../src/api/client', () => ({
  default: {
    defaults: { baseURL: '/api' },
    post: vi.fn(),
  },
}));

const responseFrom = (text, ok = true, status = 200) => {
  const bytes = new TextEncoder().encode(text);
  let read = false;
  return {
    ok,
    status,
    body: ok
      ? {
          getReader: () => ({
            read: async () => {
              if (read) return { done: true, value: undefined };
              read = true;
              return { done: false, value: bytes };
            },
            releaseLock: vi.fn(),
          }),
        }
      : undefined,
    json: async () => ({ code: 'MODEL_UNAVAILABLE', error: '模型不可用' }),
  };
};

describe('chat streaming API', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    useAuthStore().setApiKey('stream-token');
    vi.restoreAllMocks();
  });

  it('parses split SSE frames and delivers start, delta, and done events', async () => {
    const frames = [
      'event: start\ndata: {"type":"start"}\n\n',
      'event: delta\ndata: {"type":"delta","text":"部分"}\n\n',
      'event: done\ndata: {"type":"done","reply":"完整回答"}\n\n',
    ].join('');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(responseFrom(frames)));
    const events = [];

    await streamChat('session-1', 'question', {
      onEvent: (event, data) => events.push({ event, data }),
    });

    expect(fetch).toHaveBeenCalledWith(
      '/api/llm/chat/stream',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer stream-token' },
      }),
    );
    expect(events).toEqual([
      { event: 'start', data: { type: 'start' } },
      { event: 'delta', data: { type: 'delta', text: '部分' } },
      { event: 'done', data: { type: 'done', reply: '完整回答' } },
    ]);
  });

  it('exposes structured API errors for non-stream responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(responseFrom('', false, 503)));

    await expect(streamChat('session-1', 'question')).rejects.toMatchObject({
      response: {
        status: 503,
        data: { code: 'MODEL_UNAVAILABLE' },
      },
    });
  });
});
