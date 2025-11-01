<template>
  <div class="chat-message" :class="{ 'user-message': message.isUser }">
    <n-avatar :color="message.isUser ? '#e8f0fe' : 'transparent'" class="chat-avatar">
      <n-icon v-if="message.isUser" :component="UserIcon" color="#4285f4" size="24" />
      <n-icon v-else size="24" class="gemini-icon">
        <SparklesIcon />
      </n-icon>
    </n-avatar>
    <div class="message-content">
        <!-- Add markdown rendering classes -->
        <div v-if="isMarkdown" v-html="renderedMarkdown" class="markdown-body"></div>
        <div v-else class="text-body">{{ message.content }}</div>
        <div class="timestamp">{{ message.timestamp }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { NAvatar, NIcon } from 'naive-ui';
import { PersonCircleOutline as UserIcon, SparklesOutline as SparklesIcon } from '@vicons/ionicons5';
import MarkdownIt from 'markdown-it';
import 'highlight.js/styles/atom-one-dark.css'; // Import a code highlighting style
import hljs from 'highlight.js';

const props = defineProps({
  message: {
    type: Object,
    required: true,
  },
});

// Initialize MarkdownIt with highlighting
const md = new MarkdownIt({
  highlight: (str, lang) => {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre class="hljs"><code>' +
               hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
               '</code></pre>';
      } catch (__) {}
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(str) + '</code></pre>';
  }
});

const isMarkdown = computed(() => !props.message.isUser);

const renderedMarkdown = computed(() => {
    return md.render(props.message.content || '');
});

</script>

<style scoped>
.chat-message {
  display: flex;
  margin-bottom: 24px;
  gap: 16px;
}

.chat-avatar {
  flex-shrink: 0;
}

.gemini-icon {
    background: -webkit-linear-gradient(135deg, #4285f4, #9b59b6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.message-content {
  flex-grow: 1;
  max-width: 100%;
}

/* User message text bubble */
.user-message .text-body {
  background-color: #e8f0fe; /* Brighter user message bubble */
  padding: 12px 16px;
  border-radius: 8px;
  display: inline-block; /* Fit content */
  max-width: 90%;
  line-height: 1.6;
}

/* Bot message (markdown) */
.markdown-body {
  line-height: 1.7;
  color: #3c4043;
}

.timestamp {
    font-size: 0.75rem;
    color: #888;
    margin-top: 8px;
}

/* Global styles for rendered markdown */
:global(.markdown-body > *:first-child) {
    margin-top: 0;
}
:global(.markdown-body p) {
    margin-bottom: 1rem;
}
:global(.markdown-body pre) {
    background-color: #282c34; /* Dark background for code */
    color: #abb2bf;
    padding: 16px;
    border-radius: 8px;
    overflow-x: auto;
}
:global(.markdown-body code) {
    font-family: 'Courier New', Courier, monospace;
}
:global(.markdown-body pre code) {
    background: none;
    padding: 0;
}
:global(.markdown-body :not(pre) > code) {
    background-color: #e8f0fe; /* Brighter code snippet bg */
    color: #c7254e;
    padding: 2px 4px;
    border-radius: 4px;
}
:global(.markdown-body ul, .markdown-body ol) {
    padding-left: 24px;
}
</style>
