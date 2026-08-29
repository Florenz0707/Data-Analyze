# Vue 前端

使用 Node.js 20.20.2、npm 10.9.8 和已锁定的 `package-lock.json`。

```bash
npm ci
npm run dev
npm run build
npm run lint
npm run format:check
npm run test
npm run test:coverage
npm run test:e2e
```

单元测试使用 Vitest、Vue Test Utils 和 jsdom；E2E 使用 Playwright，并通过路由 Mock 后端接口，不访问真实模型。

开发环境 API 默认使用 `/api`，可通过 `.env` 中的 `VITE_API_BASE_URL` 覆盖。
