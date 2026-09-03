import { beforeEach, describe, expect, it, vi } from 'vitest';
import { attachTraceContext, extractTraceContext, reportClientError } from '../src/api/errors';

describe('client observability', () => {
  beforeEach(() => {
    delete globalThis.__APP_ERROR_REPORTER__;
  });

  it('extracts trace and request IDs from fetch-style headers', () => {
    expect(
      extractTraceContext({
        headers: {
          get: (name) => ({ 'x-trace-id': 'trace-1', 'x-request-id': 'request-1' })[name],
        },
      }),
    ).toEqual({ traceId: 'trace-1', requestId: 'request-1' });
  });

  it('attaches response trace context to an API error', () => {
    const error = new Error('failed');
    error.response = {
      headers: { 'x-trace-id': 'trace-2', 'x-request-id': 'request-2' },
    };

    expect(attachTraceContext(error)).toBe(error);
    expect(error.traceId).toBe('trace-2');
    expect(error.requestId).toBe('request-2');
  });

  it('reports a bounded client error with the originating trace ID', () => {
    const reporter = vi.fn();
    globalThis.__APP_ERROR_REPORTER__ = reporter;
    const error = new Error('request failed Bearer secret-token');
    error.traceId = 'trace-3';
    error.requestId = 'request-3';
    error.response = { data: { code: 'INTERNAL_ERROR' } };

    const report = reportClientError(error, { source: 'test' });

    expect(reporter).toHaveBeenCalledWith(report);
    expect(report).toMatchObject({
      event: 'client.error',
      trace_id: 'trace-3',
      request_id: 'request-3',
      code: 'INTERNAL_ERROR',
      source: 'test',
    });
    expect(report.message).not.toContain('secret-token');
  });
});
