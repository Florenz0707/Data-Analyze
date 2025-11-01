import apiClient from './client';

export const chat = (sessionId, userInput, useHistory = 'auto') => {
  return apiClient.post('/llm/chat', {
    session_id: sessionId,
    user_input: userInput,
    use_history: useHistory
  });
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
}
