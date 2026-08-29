import { defineConfig } from 'vitest/config';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './tests/setup.js',
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/api/**/*.js', 'src/stores/**/*.js'],
      exclude: ['**/node_modules/**', '**/dist/**', '**/tests/**'],
      thresholds: {
        lines: 75,
        functions: 75,
        statements: 75,
      },
    },
    exclude: ['**/node_modules/**', '**/dist/**', '**/tests/e2e/**'],
  },
});
