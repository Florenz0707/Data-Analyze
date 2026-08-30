import { describe, expect, it } from 'vitest';
import { getApiErrorMessage } from '../src/api/errors';

describe('API error messages', () => {
  it('maps stable backend codes to actionable messages', () => {
    expect(
      getApiErrorMessage({ response: { data: { code: 'MODEL_UNAVAILABLE', error: 'raw' } } }),
    ).toContain('切换模型');
  });

  it('falls back to backend text or a generic message', () => {
    expect(getApiErrorMessage({ response: { data: { error: '具体错误' } } })).toBe('具体错误');
    expect(getApiErrorMessage({})).toBe('操作失败，请稍后再试。');
  });
});
