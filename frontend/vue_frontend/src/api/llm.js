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
    data: { model_name: modelName } // DELETE 请求体通常在 data 属性中
  });
};
