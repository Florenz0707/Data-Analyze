import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  authStore: { apiKey: 'token', clearApiKey: vi.fn() },
  axiosCreate: vi.fn(),
  requestUse: vi.fn(),
  responseUse: vi.fn(),
}));

vi.hoisted(() => {
  mocks.axiosCreate.mockReturnValue({
    interceptors: {
      request: { use: mocks.requestUse },
      response: { use: mocks.responseUse },
    },
  });
  return undefined;
});

vi.mock('axios', () => ({ default: { create: mocks.axiosCreate } }));
vi.mock('../src/stores/auth', () => ({ useAuthStore: () => mocks.authStore }));

import apiClient from '../src/api/client';

describe('API client', () => {
  beforeEach(() => {
    mocks.authStore.clearApiKey.mockClear();
    mocks.authStore.apiKey = 'token';
  });

  it('configures the base client and adds the bearer token', () => {
    expect(mocks.axiosCreate).toHaveBeenCalledWith({
      baseURL: '/api',
      headers: { 'Content-Type': 'application/json' },
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
});
