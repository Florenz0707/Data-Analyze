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

      <!-- 模型选择 -->
      <div class="model-selection">
        <h3>模型选择</h3>
        <div class="form-group">
          <label for="provider">提供方</label>
          <select id="provider" v-model="selectedProvider" @change="onProviderChange">
            <option :value="null" disabled>选择一个提供方</option>
            <option v-for="p in providers" :key="p" :value="p">{{ p }}</option>
          </select>
        </div>
        <div class="form-group" v-if="availableModels.length > 0">
          <label for="model">模型</label>
          <select id="model" v-model="selectedModel">
            <option :value="null" disabled>选择一个模型</option>
            <option v-for="m in availableModels" :key="m" :value="m">{{ m }}</option>
          </select>
        </div>
        <div class="form-group" v-else-if="selectedProvider && !isLocalProvider">
           <p class="model-info">将使用此提供方的默认远程模型。</p>
        </div>
        <button 
          class="primary" 
          @click="handleSelectModel" 
          :disabled="!selectedProvider || modelSelectLoading">
          <span v-if="modelSelectLoading" class="loading"></span>
          <span v-else>应用模型</span>
        </button>
        <p v-if="currentModelInfo" class="current-model">
          当前: {{ currentModelInfo.provider }} 
          <span v-if="currentModelInfo.model"> ({{ currentModelInfo.model }})</span>
        </p>
      </div>

      <div class="user-info">
        <div class="user-actions">
          <button class="secondary" @click="showClearConfirm = true">
            清空当前会话
          </button>
          <button class="danger" @click="showLogoutConfirm = true">
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

      <div class="messages-container" ref="messagesContainerRef">
        <div v-if="messages.length === 0 && !loading" class="empty-state">
          开始与 DeepSeek-KAI.v.0.0.1 的对话吧！
        </div>

        <div 
          v-for="(msg, index) in messages" 
          :key="msg.id || index" 
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

    <!-- 清空历史确认对话框 -->
    <div v-if="showClearConfirm" class="dialog-overlay" @click.self="showClearConfirm = false">
      <div class="dialog">
        <h3>确认清空</h3>
        <p>确定要清空会话 "<strong>{{ currentSession }}</strong>" 的历史记录吗？</p>
        <div class="dialog-buttons">
          <button class="secondary" @click="showClearConfirm = false">取消</button>
          <button class="danger" @click="executeClearHistory">清空</button>
        </div>
      </div>
    </div>

    <!-- 退出登录确认对话框 -->
    <div v-if="showLogoutConfirm" class="dialog-overlay" @click.self="showLogoutConfirm = false">
      <div class="dialog">
        <h3>确认退出</h3>
        <p>确定要退出登录吗？</p>
        <div class="dialog-buttons">
          <button class="secondary" @click="showLogoutConfirm = false">取消</button>
          <button class="danger" @click="executeLogout">退出</button>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup>
import { onMounted, computed, ref, nextTick, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useStore } from '../store';
import api from '../api';
import SessionList from '../components/SessionList.vue';
import ChatInput from '../components/ChatInput.vue';
import markdownit from 'markdown-it';

const md = markdownit({
  html: true,
  linkify: true,
  typographer: true,
});

const store = useStore();
const router = useRouter();

const messagesContainerRef = ref(null);
const sessions = computed(() => store.sessions);
const currentSession = computed(() => store.currentSession);
const messages = computed(() => store.messages[currentSession.value] || []);
const loading = computed(() => store.loading);
const error = computed(() => store.error);

const providers = ref([]);
// (FIXED) 保持 hf, 尽管 api 返回 transformers
const localModels = ref({ hf: [], ollama: [] }); 
const selectedProvider = ref(null);
const selectedModel = ref(null);
const currentModelInfo = ref(null); 
const modelSelectLoading = ref(false);

const showClearConfirm = ref(false);
const showLogoutConfirm = ref(false);

// --- 计算属性 ---

const isLocalProvider = computed(() => {
  return selectedProvider.value === 'hf' || selectedProvider.value === 'ollama';
});

const availableModels = computed(() => {
  if (selectedProvider.value === 'hf') {
    return localModels.value.hf;
  }
  if (selectedProvider.value === 'ollama') {
    return localModels.value.ollama;
  }
  return [];
});

// --- 方法 ---

const renderMarkdown = (content) => {
  return md.render(content || '');
};

const scrollToBottom = async () => {
  await nextTick();
  const container = messagesContainerRef.value;
  if (container) {
    container.scrollTop = container.scrollHeight;
  }
};

const loadHistory = async (sessionId) => {
  if (!sessionId) return;
  // (FIXED) 仅在 store 中没有消息时才加载
  if (store.messages[sessionId] && store.messages[sessionId].length > 0) {
    scrollToBottom();
    return;
  }
  
  try {
    store.setLoading(true);
    store.setError('');
    const response = await api.getHistory(sessionId);
    let historyMessages = [];
    try {
      const parsedHistory = JSON.parse(response.data.history || '[]'); 
      historyMessages = parsedHistory.map(item => ({
        content: item.content,
        isUser: item.role === 'user',
        timestamp: new Date().toLocaleString() // 假设
      }));
    } catch (e) {
      console.error("解析历史记录失败:", e);
      store.setError('解析历史记录失败');
    }
    store.loadHistory(sessionId, historyMessages);
    scrollToBottom();
  } catch (err) {
    store.setError(err.response?.data?.error || '加载历史记录失败');
  } finally {
    store.setLoading(false);
  }
};

const loadProviders = async () => {
  try {
    store.setError('');
    const response = await api.getProviders();
    providers.value = response.data.providers || [];
    // (FIXED) API schema 中 hf 叫 transformers, 但前端统一用 hf
    // providers.value = response.data.providers.map(p => p === 'transformers' ? 'hf' : p) || [];
    // ^^^ 修正: providers 列表应该就是 providers, hf/transformers 的转换在 local_models
    
    // 默认选择第一个
    if (providers.value.length > 0 && !selectedProvider.value) {
      selectedProvider.value = providers.value[0];
    }
  } catch (err) {
    store.setError(err.response?.data?.error || '加载模型提供方失败');
  }
};

const loadLocalModels = async () => {
  try {
    store.setError('');
    const response = await api.getLocalModels();
    // (FIXED) 将 API 的 'transformers' 映射到本地的 'hf'
    localModels.value.hf = response.data.transformers || [];
    localModels.value.ollama = response.data.ollama || [];
  } catch (err) {
    store.setError(err.response?.data?.error || '加载本地模型失败');
  }
};

const selectDefaultModel = async () => {
  await loadProviders();
  await loadLocalModels();

  // (FIXED) 检查 provider 列表是否包含 'hf' 或 'ollama'
  const defaultProvider = providers.value.includes('hf') ? 'hf' : (providers.value.includes('ollama') ? 'ollama' : providers.value[0]);
  
  if (defaultProvider) {
    let defaultModel = null;
    
    if (defaultProvider === 'hf' && localModels.value.hf.length > 0) {
      defaultModel = localModels.value.hf[0];
    } else if (defaultProvider === 'ollama' && localModels.value.ollama.length > 0) {
      defaultModel = localModels.value.ollama[0];
    }
    
    selectedProvider.value = defaultProvider;
    selectedModel.value = defaultModel;
    
    await handleSelectModel();
  }
};

// (已修改) onMounted 改为 async
onMounted(async () => {
  // (NEW) 1. 检查 apiKey, 没有则跳转
  if (!store.apiKey) {
    router.push('/login');
    return;
  }

  // (NEW) 2. 异步初始化用户 Store (从 API 获取会话)
  await store.initializeUserStore();

  // (NEW) 3. store 初始化后, 加载模型并加载历史
  if (store.isInitialized) {
    await selectDefaultModel();
    if (currentSession.value) {
      await loadHistory(currentSession.value);
    }
  }
});

watch(messages, () => {
  scrollToBottom();
}, { deep: true });

// --- 事件处理 ---

const onProviderChange = () => {
  selectedModel.value = null;
  if (availableModels.value.length > 0) {
    selectedModel.value = availableModels.value[0];
  }
};

const handleSelectModel = async () => {
  if (!selectedProvider.value) return;

  const modelToSelect = isLocalProvider.value ? selectedModel.value : null;

  if (isLocalProvider.value && !modelToSelect) {
     store.setError('请为本地提供方选择一个模型');
     return;
  }
  
  modelSelectLoading.value = true;
  store.setError('');
  try {
    // (FIXED) 如果 provider 是 'hf', 发送 'transformers' 给 API
    const providerToSend = selectedProvider.value === 'hf' ? 'transformers' : selectedProvider.value;
    const response = await api.selectModel(providerToSend, modelToSelect);
    
    // (FIXED) API 返回 'transformers', 存为 'hf'
    currentModelInfo.value = {
      provider: response.data.provider === 'transformers' ? 'hf' : response.data.provider,
      model: response.data.model
    };

  } catch (err) {
    store.setError(err.response?.data?.error || '选择模型失败');
  } finally {
    modelSelectLoading.value = false;
  }
};

const handleSelectSession = async (sessionId) => {
  store.setCurrentSession(sessionId);
  await loadHistory(sessionId);
};

// (已修改) 处理删除会话, 增加 deleteSession API 调用
const handleDeleteSession = async (sessionId) => {
  try {
    store.setError('');
    // (NEW) 1. 从后端删除会话
    await api.deleteSession(sessionId);
    // 2. 清空后端历史 (也许 deleteSession 会自动做这个, 但以防万一)
    await api.clearHistory(sessionId); 
    // 3. 从 store 移除
    store.removeSession(sessionId); 
    // 4. (NEW) 切换到新会话后加载其历史
    if(currentSession.value) {
      await loadHistory(currentSession.value);
    }
  } catch (err) {
    store.setError(err.response?.data?.error || '删除会话失败');
  }
};

// (已修改) 处理创建会话, store.addSession 现在是 async
const handleCreateSession = async (sessionId) => {
  await store.addSession(sessionId); // 内部已调用 setCurrentSession
  // 新会话, messages 自动为空, 无需调用 loadHistory
};

const handleSendMessage = async (content) => {
  if (!currentModelInfo.value) {
    store.setError('请先选择一个模型再开始聊天');
    return;
  }
  
  store.addMessage(currentSession.value, true, content);

  try {
    store.setLoading(true);
    store.setError('');
    const response = await api.chat(currentSession.value, content);
    store.addMessage(currentSession.value, false, response.data.reply);
  } catch (err) {
    store.setError(err.response?.data?.error || '发送消息失败');
  } finally {
    store.setLoading(false);
  }
};

const executeClearHistory = async () => {
  showClearConfirm.value = false;
  try {
    store.setError('');
    await api.clearHistory(currentSession.value);
    store.clearSessionMessages(currentSession.value);
  } catch (err) {
    store.setError(err.response?.data?.error || '清空历史记录失败');
  }
};

const executeLogout = () => {
  showLogoutConfirm.value = false;
  store.clearApiKey();
  router.push('/login');
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

.model-selection {
  padding: 1rem;
  border-top: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.model-selection h3 {
  margin-top: 0;
  margin-bottom: 0.5rem;
  font-size: 1.1rem;
  font-weight: 600;
}
.model-selection .form-group {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.model-selection label {
  font-size: 0.9rem;
  font-weight: 500;
  color: var(--text-secondary);
}
.model-selection select {
  width: 100%;
}
.model-selection .model-info {
  font-size: 0.85rem;
  color: var(--text-secondary);
  margin: 0.25rem 0;
}
.model-selection .current-model {
  font-size: 0.85rem;
  color: var(--primary-color);
  font-weight: 500;
  margin-top: 0.5rem;
  text-align: center;
}

.user-info {
  margin-top: auto; 
  padding: 1rem;
  border-top: 1px solid var(--border-color);
}

.user-actions {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.user-actions .secondary,
.user-actions .danger {
  width: 100%;
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
  font-size: 1.5rem;
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
  /* (FIXED) Aligned to center */
  margin: 1rem auto;
  color: var(--text-secondary);
}

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

.bot-message {
  align-self: flex-start;
  border-bottom-left-radius: 4px;
}

.dialog-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 100;
}

.dialog {
  background-color: var(--card-bg);
  border-radius: var(--radius);
  padding: 1.5rem;
  width: 100%;
  max-width: 400px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}

.dialog h3 {
  margin-top: 0;
  margin-bottom: 1rem;
  font-size: 1.25rem;
}

.dialog p {
  margin-bottom: 1.5rem;
  color: var(--text-secondary);
  line-height: 1.5;
}

.dialog-buttons {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

/* Markdown 内容的深度样式 */
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
  background-color: #2d2d2d;
  color: #f8f8f2;
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
