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

const readHeader = (headers, name) => {
  if (!headers) return null;
  if (typeof headers.get === 'function') return headers.get(name);
  return headers[name] || headers[name.toLowerCase()] || null;
};

export function extractTraceContext(source) {
  const headers = source?.headers;
  return {
    traceId: readHeader(headers, 'x-trace-id') || source?.data?.trace_id || null,
    requestId: readHeader(headers, 'x-request-id') || source?.data?.request_id || null,
  };
}

export function attachTraceContext(error, source = error?.response) {
  const { traceId, requestId } = extractTraceContext(source);
  if (traceId) error.traceId = traceId;
  if (requestId) error.requestId = requestId;
  return error;
}

const safeClientMessage = (value) => {
  if (!value) return '未知客户端错误';
  return String(value)
    .replace(/Bearer\s+[^\s,;]+/gi, 'Bearer [REDACTED]')
    .replace(
      /((?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret))\s*[:=]\s*[^\s,;]+/gi,
      '$1: [REDACTED]',
    )
    .slice(0, 300);
};

export function reportClientError(error, context = {}) {
  const { traceId, requestId } = extractTraceContext(error?.response);
  const report = Object.freeze({
    event: 'client.error',
    trace_id: context.traceId || error?.traceId || traceId,
    request_id: context.requestId || error?.requestId || requestId,
    code: error?.response?.data?.code || null,
    message: safeClientMessage(error?.message),
    source: context.source || 'client',
    route: typeof window !== 'undefined' ? window.location.pathname : null,
  });
  const reporter = globalThis.__APP_ERROR_REPORTER__;
  if (typeof reporter === 'function') {
    try {
      reporter(report);
    } catch {
      // Error reporting must never replace the original user-facing failure.
    }
  }
  if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
    window.dispatchEvent(new CustomEvent('app:error', { detail: report }));
  }
  return report;
}

export function getApiErrorMessage(error, fallback = '操作失败，请稍后再试。') {
  const data = error?.response?.data;
  return ERROR_MESSAGES[data?.code] || data?.error || error?.message || fallback;
}
