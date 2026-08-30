const ERROR_MESSAGES = Object.freeze({
  VALIDATION_ERROR: '请求参数有误，请检查后重试。',
  AUTH_REQUIRED: '登录状态已失效，请重新登录。',
  AUTH_INVALID: '认证信息无效，请重新登录。',
  AUTH_FORBIDDEN: '当前账号无权执行此操作。',
  RESOURCE_NOT_FOUND: '请求的资源不存在。',
  RESOURCE_CONFLICT: '资源已存在，请检查输入。',
  RATE_LIMITED: '请求过于频繁，请稍后再试。',
  MODEL_UNAVAILABLE: '模型服务暂不可用，请稍后重试或切换模型。',
  INTERNAL_ERROR: '服务暂时异常，请稍后再试。',
});

export function getApiErrorMessage(error, fallback = '操作失败，请稍后再试。') {
  const data = error?.response?.data;
  return ERROR_MESSAGES[data?.code] || data?.error || error?.message || fallback;
}
