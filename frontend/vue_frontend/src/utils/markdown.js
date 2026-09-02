import MarkdownIt from 'markdown-it';
import hljs from 'highlight.js';

const MAX_RENDER_CACHE_SIZE = 100;
const renderedMarkdownCache = new Map();

export const markdownRenderer = new MarkdownIt({
  html: false,
  highlight: (str, lang) => {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return (
          '<pre class="hljs"><code>' +
          hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
          '</code></pre>'
        );
      } catch {
        // Fall back to escaped plain code below when language highlighting fails.
      }
    }
    return '<pre class="hljs"><code>' + markdownRenderer.utils.escapeHtml(str) + '</code></pre>';
  },
});

export const renderMarkdown = (content, { cache = true } = {}) => {
  const source = content || '';
  if (!cache) return markdownRenderer.render(source);

  const cached = renderedMarkdownCache.get(source);
  if (cached !== undefined) {
    renderedMarkdownCache.delete(source);
    renderedMarkdownCache.set(source, cached);
    return cached;
  }

  const rendered = markdownRenderer.render(source);
  renderedMarkdownCache.set(source, rendered);
  if (renderedMarkdownCache.size > MAX_RENDER_CACHE_SIZE) {
    renderedMarkdownCache.delete(renderedMarkdownCache.keys().next().value);
  }
  return rendered;
};

export const clearMarkdownCache = () => renderedMarkdownCache.clear();
