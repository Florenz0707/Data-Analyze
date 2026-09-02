import apiClient from './client';

export const getSessionList = () => {
  return apiClient.get('/sessions');
};

export const createSession = (sessionId) => {
  return apiClient.post('/sessions', { session_id: sessionId });
};

export const deleteSession = (sessionId) => {
  return apiClient.request({
    method: 'DELETE',
    url: '/sessions',
    data: { session_id: sessionId },
  });
};

export const getHistory = (sessionId, params = {}) => {
  return apiClient.get('/sessions/history', {
    params: { session_id: sessionId, ...params },
  });
};

export const clearHistory = (sessionId) => {
  return apiClient.delete('/sessions/history', { params: { session_id: sessionId } });
};
