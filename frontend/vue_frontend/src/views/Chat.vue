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

        <ChatMessage
          v-for="msg in messages"
          :key="msg.id"
          :is-user="msg.isUser"
          :content="msg.content"
          :timestamp="msg.timestamp"
        />

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
import ChatMessage from '../components/ChatMessage.vue';
import ChatInput from '../components/ChatInput.vue';

const store = useStore();
const router = useRouter();

// 计算属性
const sessions = computed(() => store.sessions);
const currentSession = computed(() => store.currentSession);
const messages = computed(() => store.messages[currentSession.value] || []);
const loading = computed(() => store.loading);
const error = computed(() => store.error);

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
  loadHistory(currentSession.value);
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
  store.clearSessionMessages(sessionId);
};

// 处理发送消息
const handleSendMessage = async (content) => {
  // 添加用户消息到界面
  store.addMessage(currentSession.value, true, content);

  try {
    store.setLoading(true);
    // 调用API发送消息
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
</style>
