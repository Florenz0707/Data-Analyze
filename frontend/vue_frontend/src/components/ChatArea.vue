<template>
  <div class="chat-area">
    <div class="header">
      <!-- Minimal header -->
      <n-h2>{{ chatStore.currentSession || 'Chat' }}</n-h2>
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
// Removed ModelStore imports
import ChatMessage from './ChatMessage.vue';
import MessageInput from './MessageInput.vue';
import * as api from '../api';

const chatStore = useChatStore();
const appStore = useAppStore();
// Removed ModelStore setup

const messages = computed(() => chatStore.messages[chatStore.currentSession] || []);
const scrollbarRef = ref(null);

// --- Model Selection Logic Removed ---

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
    chatStore.loadHistory(newSession).then(scrollToBottom);
  }
}, { immediate: true });

watch(messages, () => {
  scrollToBottom();
}, { deep: true });


const handleSendMessage = async (text) => {
  const userMessage = { isUser: true, content: text, timestamp: new Date().toLocaleString() };
  chatStore.addMessage(chatStore.currentSession, userMessage);

  appStore.setLoading(true);
  try {
    const response = await api.chat(chatStore.currentSession, text);
    const botMessage = { isUser: false, content: response.data.reply, timestamp: new Date().toLocaleString() };
    chatStore.addMessage(chatStore.currentSession, botMessage);
  } catch (error) {
    appStore.setError('Failed to send message.');
  } finally {
    appStore.setLoading(false);
  }
};
</script>

<style scoped>
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
