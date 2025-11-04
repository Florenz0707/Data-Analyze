<template>
  <div class="message-input">
    <n-input
      v-model:value="text"
      type="textarea"
      placeholder="Type your message here..."
      :autosize="{ minRows: 1, maxRows: 5 }"
      @keypress.enter.prevent="handleSend"
      class="chat-textarea"
    />
    <n-button 
      circle 
      @click="handleSend" 
      :disabled="!text.trim() || appStore.loading" 
      class="send-button"
      :loading="appStore.loading"
    >
      <template #icon>
        <n-icon :component="SendIcon" />
      </template>
    </n-button>
  </div>
</template>

<script setup>
// 修改：导入 watch
import { ref, watch } from 'vue';
import { NInput, NButton, NIcon } from 'naive-ui';
import { SendOutline as SendIcon } from '@vicons/ionicons5';
import { useAppStore } from '../stores/app';
// 修改：导入 chatStore
import { useChatStore } from '../stores/chat';

const emit = defineEmits(['send']);
const appStore = useAppStore();
// 修改：设置 chatStore
const chatStore = useChatStore();
const text = ref('');

// 修改：添加 watch 来监听会话变更
watch(() => chatStore.currentSession, (newSession) => {
  // 当切换到新的临时会话时，清空输入框
  if (newSession === 'temp:new_chat') {
    text.value = '';
  }
});

const handleSend = () => {
  if (text.value.trim() && !appStore.loading) {
    emit('send', text.value);
    text.value = '';
  }
};
</script>

<style scoped>
/* ... 样式保持不变 ... */
.message-input {
  display: flex;
  align-items: flex-end; /* Align to bottom for multiline */
  gap: 12px;
  background-color: #f8fafd; /* Brighter background */
  border-radius: 24px; /* Rounded pill shape */
  padding: 8px 8px 8px 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}

.chat-textarea {
  background-color: transparent !important;
}

/* Remove default input styling */
.chat-textarea :deep(.n-input__border),
.chat-textarea :deep(.n-input__state-border) {
  display: none;
}

.chat-textarea :deep(.n-input__input-el) {
    padding-right: 0;
}

.send-button {
  flex-shrink: 0;
  /* Bright gradient send button */
  background: linear-gradient(135deg, #4285f4, #9b59b6);
  color: white;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}
.send-button:hover {
  background: linear-gradient(135deg, #3a75d9, #8a4eae);
}
.send-button.n-button--disabled {
  background: #f0f0f0;
  color: #aaa;
  opacity: 1;
}
</style>
