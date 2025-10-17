<template>
  <div class="chat-input-container">
    <textarea
      v-model="message"
      class="chat-input"
      placeholder="输入消息..."
      @keyup.enter.exact="sendMessage"
      @keyup.enter.shift="addNewline"
      :disabled="loading"
    ></textarea>
    <div class="input-actions">
      <button
        class="secondary"
        @click="clearInput"
        :disabled="!message.trim() || loading"
      >
        清除
      </button>
      <button
        class="primary"
        @click="sendMessage"
        :disabled="!message.trim() || loading"
      >
        <span v-if="loading" class="loading"></span>
        <span v-else>发送</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, defineProps, defineEmits } from 'vue';

const props = defineProps({
  loading: {
    type: Boolean,
    default: false
  }
});

const emits = defineEmits(['send']);

const message = ref('');

const sendMessage = () => {
  const content = message.value.trim();
  if (content) {
    emits('send', content);
    message.value = '';
  }
};

const clearInput = () => {
  message.value = '';
};

const addNewline = () => {
  message.value += '\n';
};
</script>

<style scoped>
.chat-input-container {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 1rem;
  border-top: 1px solid var(--border-color);
}

.chat-input {
  min-height: 80px;
  resize: vertical;
  width: 100%;
}

.input-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

.loading {
  margin-right: 0.5rem;
  vertical-align: middle;
}
</style>
