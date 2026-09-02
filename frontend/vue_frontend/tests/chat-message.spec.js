import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import ChatMessage from '../src/components/ChatMessage.vue';
import { clearMarkdownCache, renderMarkdown } from '../src/utils/markdown';

describe('ChatMessage', () => {
  it('caches completed Markdown rendering', () => {
    clearMarkdownCache();
    const first = renderMarkdown('**cached**');
    const second = renderMarkdown('**cached**');

    expect(second).toBe(first);
  });

  it('escapes raw HTML in assistant Markdown', () => {
    const wrapper = mount(ChatMessage, {
      props: {
        message: {
          isUser: false,
          content: '<script>alert(1)</script><img src=x onerror=alert(2)>',
          timestamp: 'now',
        },
      },
      global: {
        stubs: {
          NAvatar: { template: '<div><slot /></div>' },
          NIcon: { template: '<span><slot /></span>' },
        },
      },
    });

    expect(wrapper.find('.markdown-body').html()).not.toContain('<script>');
    expect(wrapper.find('.markdown-body').html()).not.toContain('<img');
    expect(wrapper.text()).toContain('<script>alert(1)</script>');
  });

  it('renders user content as text rather than HTML', () => {
    const wrapper = mount(ChatMessage, {
      props: {
        message: { isUser: true, content: '<b>not markup</b>', timestamp: 'now' },
      },
      global: {
        stubs: {
          NAvatar: { template: '<div><slot /></div>' },
          NIcon: { template: '<span><slot /></span>' },
        },
      },
    });

    expect(wrapper.find('.text-body').element.innerHTML).toBe('&lt;b&gt;not markup&lt;/b&gt;');
  });
});
