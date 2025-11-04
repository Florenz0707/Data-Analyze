<template>
  <div class="chat-area">
    <div class="header">
      <!-- 修改：使用 getter 显示会话名称 -->
      <n-h2>{{ sessionDisplayName }}</n-h2>
    </div>

    <!-- Messages container grows and scrolls -->
    <n-scrollbar class="messages-container" ref="scrollbarRef">
      <!-- Centering wrapper for messages -->
      <div class="messages-wrapper">
        <div v-if="!messages || messages.length === 0" class="empty-state">
          <!-- Gemini-style empty state -->
          <div class="empty-icon">
            <n-icon :component="SparklesIcon" size="48" />
          </div>
          <n-h1>Hello!</n-h1>
          <n-p>How can I help you today?</n-p>
        </div>
        <div v-else>
          <ChatMessage
            v-for="(message, index) in messages"
            :key="index"
            :message="message"
          />
        </div>
        <div v-if="appStore.loading" class="typing-indicator">
          <n-spin size="small" />
          <span>Assistant is typing...</span>
        </div>
      </div>
    </n-scrollbar>

    <!-- Input area sticks to the bottom -->
    <div class="input-area">
      <!-- Centering wrapper for input -->
      <div class="input-wrapper">
        <!-- Model Selection UI Removed -->
        <MessageInput @send="handleSendMessage" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick } from 'vue';
import { NScrollbar, NH2, NEmpty, NSpin, NP, NH1, NIcon } from 'naive-ui';
import { SparklesOutline as SparklesIcon } from '@vicons/ionicons5';
import { useChatStore } from '../stores/chat';
import { useAppStore } from '../stores/app';
// 导入 storeToRefs 来使用 getters
import { storeToRefs } from 'pinia';
import ChatMessage from './ChatMessage.vue';
import MessageInput from './MessageInput.vue';
// api 已移至 store
// import * as api from '../api';

const chatStore = useChatStore();
const appStore = useAppStore();

// 使用 storeToRefs 来获取响应式的 getter
const { sessionDisplayName } = storeToRefs(chatStore);
const messages = computed(() => chatStore.messages[chatStore.currentSession] || []);
const scrollbarRef = ref(null);

const scrollToBottom = () => {
    nextTick(() => {
        const scrollbar = scrollbarRef.value;
        if (scrollbar) {
            scrollbar.scrollTo({ top: scrollbar.scrollbarInstRef.contentRef.scrollHeight, behavior: 'smooth' });
        }
    });
};

watch(() => chatStore.currentSession, (newSession) => {
  if (newSession) {
    // loadHistory 现在会处理临时会话ID
    chatStore.loadHistory(newSession).then(scrollToBottom);
  } else {
    // 如果没有会话 (例如初始化时)，也确保滚动 (清空时)
    scrollToBottom();
  }
}, { immediate: true });

watch(messages, () => {
  scrollToBottom();
}, { deep: true });


// 修改：handleSendMessage 现在只调用 store action
const handleSendMessage = (text) => {
  chatStore.sendMessage(text);
};
</script>

<style scoped>
/* ... 样式保持不变 ... */
.chat-area {
  display: flex;
  flex-direction: column;
  height: 100%;
  position: relative; /* For absolute positioning of input */
  background-color: #ffffff;
}

.header {
  padding: 12px 24px;
  border-bottom: 1px solid #f0f0f0; /* Lighter border */
  flex-shrink: 0; /* Prevent header from shrinking */
  background-color: #ffffff; /* Clean white header */
}

.header .n-h2 {
    font-size: 1.25rem;
    font-weight: 500;
    margin: 0;
}

.messages-container {
  flex-grow: 1; /* Take up remaining space */
  padding-bottom: 120px; /* Adjusted padding */
}

.messages-wrapper {
  max-width: 800px;
  margin: 0 auto;
  padding: 24px;
}

.empty-state {
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    height: 60vh;
    color: #5f6368;
}
.empty-icon {
    background: -webkit-linear-gradient(135deg, #4285f4, #9b59b6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 16px;
}
.empty-state .n-h1 {
    font-size: 2.5rem;
    font-weight: 600;
    margin-bottom: 8px;
}

.input-area {
  position: absolute; /* Stick to bottom */
  bottom: 0;
  left: 0;
  right: 0;
  padding: 16px 24px;
  background-color: #ffffff;
  border-top: 1px solid #f0f0f0; /* Lighter border */
}

.input-wrapper {
  max-width: 800px;
  margin: 0 auto;
}

/* Removed model selector styles */

.typing-indicator {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 0;
    color: #5f6368;
}
</style>
