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
import { ref } from 'vue';
import { NInput, NButton, NIcon } from 'naive-ui';
import { SendOutline as SendIcon } from '@vicons/ionicons5';
import { useAppStore } from '../stores/app';

const emit = defineEmits(['send']);
const appStore = useAppStore();
const text = ref('');

const handleSend = () => {
  if (text.value.trim() && !appStore.loading) {
    emit('send', text.value);
    text.value = '';
  }
};
</script>

<style scoped>
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
