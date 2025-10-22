<template>
  <div class="chat-container">
    <div class="sidebar">
      <SessionList
        :sessions="sessions"
        :current-session="currentSession"
        @select="handleSelectSession"
        @delete="handleDeleteSession"
        @create="handleCreateSession"
      />

      <div class="user-info">
        <div class="user-actions">
          <button class="secondary" @click="handleClearHistory">
            清空当前会话
          </button>
          <button class="danger" @click="handleLogout">
            退出登录
          </button>
        </div>
      </div>
    </div>

    <div class="chat-area">
      <div class="chat-header">
        <h1>DeepSeek-KAI.v.0.0.1 聊天</h1>
        <h2>当前会话: {{ currentSession }}</h2>
      </div>

      <div v-if="error" class="error-message">{{ error }}</div>

      <div class="messages-container">
        <div v-if="messages.length === 0" class="empty-state">
          开始与 DeepSeek-KAI.v.0.0.1 的对话吧！
        </div>

        <!-- 功能1: 修改消息展示方式以支持Markdown -->
        <div 
          v-for="msg in messages" 
          :key="msg.id" 
          class="message-wrapper" 
          :class="{ 'user-message': msg.isUser, 'bot-message': !msg.isUser }">
            <div class="message-content" v-html="renderMarkdown(msg.content)"></div>
            <div class="message-timestamp">{{ msg.timestamp }}</div>
        </div>


        <div v-if="loading" class="loading-indicator">
          <div class="loading"></div>
          <p>DeepSeek-KAI.v.0.0.1 正在思考...</p>
        </div>
      </div>

      <ChatInput
        :loading="loading"
        @send="handleSendMessage"
      />
    </div>
  </div>
</template>

<script setup>
import { onMounted, computed } from 'vue';
import { useRouter } from 'vue-router';
import { useStore } from '../store';
import api from '../api';
import SessionList from '../components/SessionList.vue';
// 移除了 ChatMessage 组件的导入，因为我们直接在模板中处理消息渲染
// import ChatMessage from '../components/ChatMessage.vue';
import ChatInput from '../components/ChatInput.vue';

// 功能1: 导入 markdown-it 用于解析 Markdown
import markdownit from 'markdown-it';

// 初始化 markdown-it
const md = markdownit({
  html: true, // 允许HTML标签
  linkify: true, // 自动转换链接
  typographer: true, // 启用智能标点符号
});

const store = useStore();
const router = useRouter();

// 计算属性
const sessions = computed(() => store.sessions);
const currentSession = computed(() => store.currentSession);
const messages = computed(() => store.messages[currentSession.value] || []);
const loading = computed(() => store.loading);
const error = computed(() => store.error);

// 功能1: 创建一个渲染 Markdown 的函数
const renderMarkdown = (content) => {
  // 确保内容是字符串类型
  return md.render(content || '');
};

// 初始化加载历史记录
const loadHistory = async (sessionId) => {
  try {
    store.setLoading(true);
    const response = await api.getHistory(sessionId);
    store.loadHistory(sessionId, response.data.history);
  } catch (err) {
    store.setError(err.response?.data?.error || '加载历史记录失败');
  } finally {
    store.setLoading(false);
  }
};

// 挂载时加载当前会话历史
onMounted(() => {
  if (currentSession.value) {
    loadHistory(currentSession.value);
  }
});

// 处理选择会话
const handleSelectSession = async (sessionId) => {
  store.setCurrentSession(sessionId);
  await loadHistory(sessionId);
};

// 处理删除会话
const handleDeleteSession = async (sessionId) => {
  try {
    await api.clearHistory(sessionId);
    store.removeSession(sessionId);
    store.clearSessionMessages(sessionId);
  } catch (err) {
    store.setError(err.response?.data?.error || '删除会话失败');
  }
};

// 处理创建会话
const handleCreateSession = (sessionId) => {
  store.addSession(sessionId);
  store.setCurrentSession(sessionId); // 创建后自动切换到新会话
};

// 处理发送消息
const handleSendMessage = async (content) => {
  // 添加用户消息到界面
  store.addMessage(currentSession.value, true, content);

  try {
    store.setLoading(true);
    
    // 功能2: 发送完整的对话历史到API
    // messages.value 包含了刚刚添加的用户新消息
    const conversationHistory = messages.value;
    const response = await api.chat(currentSession.value, content);
    
    // 添加机器人回复到界面
    store.addMessage(currentSession.value, false, response.data.reply);
  } catch (err) {
    store.setError(err.response?.data?.error || '发送消息失败');
  } finally {
    store.setLoading(false);
  }
};

// 处理清空历史
const handleClearHistory = async () => {
  if (confirm(`确定要清空当前会话 "${currentSession.value}" 的历史记录吗？`)) {
    try {
      await api.clearHistory(currentSession.value);
      store.clearSessionMessages(currentSession.value);
    } catch (err) {
      store.setError(err.response?.data?.error || '清空历史记录失败');
    }
  }
};

// 处理退出登录
const handleLogout = () => {
  if (confirm('确定要退出登录吗？')) {
    store.clearApiKey();
    router.push('/login');
  }
};
</script>

<style scoped>
.chat-container {
  display: flex;
  height: 100vh;
}

.sidebar {
  width: 300px;
  display: flex;
  flex-direction: column;
  background-color: var(--card-bg);
  border-right: 1px solid var(--border-color);
}

.user-info {
  padding: 1rem;
  border-top: 1px solid var(--border-color);
}

.user-actions {
  display: flex;
  gap: 0.5rem;
}

.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-color);
}

.chat-header {
  padding: 1rem;
  background-color: var(--card-bg);
  border-bottom: 1px solid var(--border-color);
}

.chat-header h1 {
  color: var(--primary-color);
  margin-bottom: 0.25rem;
}

.chat-header h2 {
  font-size: 1rem;
  color: var(--text-secondary);
  font-weight: 500;
}

.messages-container {
  flex: 1;
  padding: 1rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.empty-state {
  margin: auto;
  color: var(--text-secondary);
  font-size: 1.25rem;
  text-align: center;
  padding: 2rem;
}

.loading-indicator {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 1rem auto;
  color: var(--text-secondary);
}

/* 新增：消息气泡样式 */
.message-wrapper {
  display: flex;
  flex-direction: column;
  margin-bottom: 1rem;
  max-width: 85%;
  align-items: flex-start;
}

.message-content {
  padding: 0.75rem 1rem;
  border-radius: 12px;
  line-height: 1.6;
  word-wrap: break-word;
  background-color: var(--card-bg);
  color: var(--text-primary);
  border: 1px solid var(--border-color);
}

.message-timestamp {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-top: 0.3rem;
  padding: 0 0.5rem;
}

/* 用户消息样式 */
.user-message {
  align-self: flex-end;
  align-items: flex-end;
}

.user-message .message-content {
  background-color: var(--primary-color);
  color: white;
  border: none;
  border-bottom-right-radius: 4px;
}

/* 机器人消息样式 */
.bot-message {
  align-self: flex-start;
  border-bottom-left-radius: 4px;
}

/* 新增：Markdown 内容的深度样式 */
/* 使用 :deep() 选择器来穿透 scoped CSS 的限制，为 v-html 渲染出的内容添加样式 */
.bot-message .message-content :deep(p) {
  margin-bottom: 0.5em;
}
.bot-message .message-content :deep(p):last-child {
  margin-bottom: 0;
}
.bot-message .message-content :deep(ul),
.bot-message .message-content :deep(ol) {
  padding-left: 1.5em;
  margin: 0.5em 0;
}
.bot-message .message-content :deep(pre) {
  background-color: #2d2d2d; /* 暗色代码块背景 */
  color: #f8f8f2; /* 亮色代码文本 */
  padding: 1em;
  border-radius: 8px;
  overflow-x: auto;
  margin: 1em 0;
}
.bot-message .message-content :deep(code) {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, Courier, monospace;
  background-color: rgba(128, 128, 128, 0.15);
  padding: 0.2em 0.4em;
  border-radius: 4px;
  font-size: 0.9em;
}
.bot-message .message-content :deep(pre) > code {
  padding: 0;
  background-color: transparent;
  font-size: 1em;
}
.bot-message .message-content :deep(blockquote) {
  border-left: 4px solid var(--border-color);
  margin: 1em 0;
  padding-left: 1em;
  color: var(--text-secondary);
}
.bot-message .message-content :deep(a) {
  color: var(--primary-color);
  text-decoration: none;
}
.bot-message .message-content :deep(a):hover {
  text-decoration: underline;
}
</style>
