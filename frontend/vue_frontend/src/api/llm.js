import apiClient from './client';
import { attachTraceContext, reportClientError } from './errors';
import { useAuthStore } from '../stores/auth';

export const chat = (sessionId, userInput, useHistory = 'auto') => {
  return apiClient.post('/llm/chat', {
    session_id: sessionId,
    user_input: userInput,
    use_history: useHistory,
  });
};

const streamError = (response, data) => {
  const error = new Error(data?.error || `Stream request failed (${response.status})`);
  error.response = { status: response.status, data };
  return attachTraceContext(error, response);
};

const readSseEvents = async (response, onEvent) => {
  if (!response.body) throw new Error('Streaming response body is unavailable');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary = buffer.indexOf('\n\n');
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        let event = 'message';
        let data = '';
        for (const line of frame.split(/\r?\n/)) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          if (line.startsWith('data:')) data += line.slice(5).trimStart();
        }
        if (data) onEvent(event, JSON.parse(data));
        boundary = buffer.indexOf('\n\n');
      }
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
};

const fetchStream = async (url, init, authStore) => {
  let response = await fetch(url, init);
  if (response.status !== 401) return response;

  try {
    const refreshed = await apiClient.post('/refresh', null, { _skipAuthRefresh: true });
    const authorization = refreshed?.headers?.authorization;
    const token = authorization?.startsWith('Bearer ') ? authorization.slice(7) : null;
    if (!token) return response;
    authStore.setApiKey(token);
    const headers = { ...init.headers, Authorization: `Bearer ${token}` };
    response = await fetch(url, { ...init, headers });
  } catch {
    // Return the original 401 so the caller gets the stable API error shape.
  }
  return response;
};

export const streamChat = async (
  sessionId,
  userInput,
  { useHistory = 'auto', messageId, signal, onEvent } = {},
) => {
  const authStore = useAuthStore();
  const baseURL = apiClient.defaults?.baseURL || '/api';
  const headers = { 'Content-Type': 'application/json' };
  if (authStore.apiKey) headers.Authorization = `Bearer ${authStore.apiKey}`;
  let response;
  try {
    response = await fetchStream(
      `${baseURL}/llm/chat/stream`,
      {
        method: 'POST',
        headers,
        credentials: 'include',
        signal,
        body: JSON.stringify({
          session_id: sessionId,
          user_input: userInput,
          use_history: useHistory,
          ...(messageId ? { message_id: messageId } : {}),
        }),
      },
      authStore,
    );
    if (!response.ok) {
      let data = {};
      try {
        data = await response.json();
      } catch {
        // Preserve the HTTP status when the gateway returned a non-JSON body.
      }
      throw streamError(response, data);
    }
    await readSseEvents(response, onEvent || (() => {}));
  } catch (error) {
    if (response) attachTraceContext(error, response);
    if (error?.name !== 'AbortError') {
      reportClientError(error, { source: 'fetch.stream' });
    }
    throw error;
  }
};

export const getProviders = () => {
  return apiClient.get('/llm/providers');
};

export const getLocalModels = () => {
  return apiClient.get('/llm/local_models');
};

export const selectModel = (provider, model) => {
  return apiClient.post('/llm/select', { provider, model });
};

export const getCurrentModel = () => {
  return apiClient.get('/llm/my');
};

// --- 新增接口 ---

/**
 * 获取用户自定义模型列表
 */
export const getCustomModels = () => {
  return apiClient.get('/llm/extern');
};

/**
 * 添加自定义模型
 * @param {object} modelData - { base_url, model_name, api_key, alias }
 */
export const addCustomModel = (modelData) => {
  return apiClient.post('/llm/extern', modelData);
};

/**
 * 删除自定义模型
 * @param {string} modelName - model_name 或 alias
 */
export const deleteCustomModel = (modelName) => {
  return apiClient.delete('/llm/extern', {
    data: { model_name: modelName }, // DELETE 请求体通常在 data 属性中
  });
};
