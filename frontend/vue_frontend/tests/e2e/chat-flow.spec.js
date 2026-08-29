import { expect, test } from '@playwright/test';

test('logs in and completes a mocked chat flow', async ({ page }) => {
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.pathname.endsWith('/users/login')) {
      await route.fulfill({
        status: 200,
        headers: { Authorization: 'Bearer e2e-token' },
        contentType: 'application/json',
        body: JSON.stringify({ message: '登录成功' }),
      });
      return;
    }

    if (url.pathname.endsWith('/sessions') && request.method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ sessions: [] }),
      });
      return;
    }

    if (url.pathname.endsWith('/sessions') && request.method() === 'POST') {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: 'mock-session' }),
      });
      return;
    }

    if (url.pathname.endsWith('/llm/chat')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ reply: 'Mock assistant answer' }),
      });
      return;
    }

    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  await page.goto('/login');
  await page.getByPlaceholder('Enter your username').fill('e2e-user');
  await page.getByPlaceholder('Enter your password').fill('e2e-password');
  await page.getByRole('button', { name: 'Login' }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText('How can I help you today?')).toBeVisible();

  await page.getByPlaceholder('Type your message here...').fill('hello');
  await page.locator('.send-button').click();

  await expect(page.getByText('Mock assistant answer')).toBeVisible();
});
