import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  authStore: { apiKey: 'token', clearApiKey: vi.fn() },
  axiosCreate: vi.fn(),
  requestUse: vi.fn(),
  responseUse: vi.fn(),
  apiClient: vi.fn(),
}));

vi.hoisted(() => {
  mocks.apiClient.interceptors = {
    request: { use: mocks.requestUse },
    response: { use: mocks.responseUse },
  };
  mocks.apiClient.post = vi.fn();
  mocks.axiosCreate.mockReturnValue(mocks.apiClient);
  return undefined;
});

vi.mock('axios', () => ({ default: { create: mocks.axiosCreate } }));
vi.mock('../src/stores/auth', () => ({ useAuthStore: () => mocks.authStore }));

import apiClient from '../src/api/client';

describe('API client', () => {
  beforeEach(() => {
    mocks.authStore.clearApiKey.mockClear();
    mocks.authStore.setApiKey = vi.fn();
    mocks.authStore.apiKey = 'token';
    mocks.apiClient.mockReset();
    mocks.apiClient.post.mockReset();
  });

  it('configures the base client and adds the bearer token', () => {
    expect(mocks.axiosCreate).toHaveBeenCalledWith({
      baseURL: '/api',
      headers: { 'Content-Type': 'application/json' },
      withCredentials: true,
    });
    const requestSuccess = mocks.requestUse.mock.calls[0][0];
    const config = requestSuccess({ headers: {} });

    expect(config.headers.Authorization).toBe('Bearer token');
    mocks.authStore.apiKey = null;
    expect(requestSuccess({ headers: {} }).headers.Authorization).toBeUndefined();
    expect(apiClient).toBeDefined();
  });

  it('rejects request errors and clears auth on unauthorized responses', async () => {
    const requestFailure = mocks.requestUse.mock.calls[0][1];
    const responseSuccess = mocks.responseUse.mock.calls[0][0];
    const responseFailure = mocks.responseUse.mock.calls[0][1];
    const error = new Error('request failed');

    await expect(requestFailure(error)).rejects.toBe(error);
    expect(responseSuccess({ data: 'ok' })).toEqual({ data: 'ok' });
    await expect(responseFailure({ response: { status: 500 } })).rejects.toEqual({
      response: { status: 500 },
    });
    await expect(responseFailure({ response: { status: 401 } })).rejects.toEqual({
      response: { status: 401 },
    });
    expect(mocks.authStore.clearApiKey).toHaveBeenCalledOnce();
  });

  it('shares one refresh request and replays concurrent unauthorized requests', async () => {
    const responseFailure = mocks.responseUse.mock.calls[0][1];
    mocks.apiClient.post.mockResolvedValue({
      headers: { authorization: 'Bearer refreshed-token' },
    });
    mocks.apiClient.mockResolvedValue({ data: 'replayed' });

    const first = responseFailure({
      response: { status: 401 },
      config: { url: '/sessions', headers: {} },
    });
    const second = responseFailure({
      response: { status: 401 },
      config: { url: '/sessions/history', headers: {} },
    });

    await expect(Promise.all([first, second])).resolves.toEqual([
      { data: 'replayed' },
      { data: 'replayed' },
    ]);
    expect(mocks.apiClient.post).toHaveBeenCalledOnce();
    expect(mocks.apiClient.post).toHaveBeenCalledWith('/refresh', null, {
      _skipAuthRefresh: true,
    });
    expect(mocks.authStore.setApiKey).toHaveBeenCalledWith('refreshed-token');
    expect(mocks.apiClient).toHaveBeenCalledTimes(2);
  });
});
