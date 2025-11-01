import apiClient from './client';

export const login = (username, password) => {
  return apiClient.post('/users/login', { username, password });
};

export const register = (username, password) => {
  return apiClient.post('/users/register', { username, password });
};

export const refreshToken = () => {
  return apiClient.post('/refresh');
};
