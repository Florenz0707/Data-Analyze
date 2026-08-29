import { afterEach } from 'vitest';
import { config } from '@vue/test-utils';

afterEach(() => {
  localStorage.clear();
});

config.global.stubs = {
  transition: false,
  'transition-group': false,
};

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}

    unobserve() {}

    disconnect() {}
  };
}
