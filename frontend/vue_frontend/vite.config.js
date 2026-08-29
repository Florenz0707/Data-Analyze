import process from 'node:process';
import { defineConfig, loadEnv } from 'vite';
import vue from '@vitejs/plugin-vue';

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const devPort = Number(env.VITE_DEV_PORT || 8082);
  const apiTarget = env.VITE_DEV_API_TARGET || 'http://localhost:8081';

  return {
    plugins: [vue()],
    server: {
      host: 'localhost',
      allowedHosts: ['localhost', '127.0.0.1'],
      port: devPort,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
    preview: {
      host: '127.0.0.1',
      allowedHosts: ['localhost', '127.0.0.1'],
    },
  };
});
