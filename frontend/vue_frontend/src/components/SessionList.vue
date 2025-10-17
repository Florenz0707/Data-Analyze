<template>
  <div class="session-list">
    <div class="session-list-header">
      <h2>会话</h2>
      <button class="primary" @click="showNewSessionDialog = true">
        <plus-icon class="icon" />
      </button>
    </div>

    <div class="session-items">
      <div
        v-for="session in sessions"
        :key="session"
        class="session-item"
        :class="{ active: session === currentSession }"
        @click="selectSession(session)"
      >
        <div class="session-name">{{ session }}</div>
        <button
          class="delete-btn"
          @click.stop="deleteSession(session)"
          title="删除会话"
        >
          <trash-icon class="icon" />
        </button>
      </div>
    </div>

    <!-- 新建会话对话框 -->
    <div v-if="showNewSessionDialog" class="dialog-overlay">
      <div class="dialog">
        <h3>新建会话</h3>
        <input
          type="text"
          v-model="newSessionName"
          placeholder="输入会话名称"
          @keyup.enter="createSession"
        />
        <div class="dialog-buttons">
          <button class="secondary" @click="showNewSessionDialog = false">取消</button>
          <button class="primary" @click="createSession">创建</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, defineProps, defineEmits } from 'vue';
import { PlusIcon, TrashIcon } from 'vue-tabler-icons';

const props = defineProps({
  sessions: {
    type: Array,
    required: true
  },
  currentSession: {
    type: String,
    required: true
  }
});

const emits = defineEmits(['select', 'delete', 'create']);

const showNewSessionDialog = ref(false);
const newSessionName = ref('');

const selectSession = (session) => {
  emits('select', session);
};

const deleteSession = (session) => {
  if (confirm(`确定要删除会话 "${session}" 吗？`)) {
    emits('delete', session);
  }
};

const createSession = () => {
  if (newSessionName.value.trim()) {
    emits('create', newSessionName.value.trim());
    newSessionName.value = '';
    showNewSessionDialog.value = false;
  }
};
</script>

<style scoped>
.session-list {
  display: flex;
  flex-direction: column;
  height: 100%;
  border-right: 1px solid var(--border-color);
}

.session-list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem;
  border-bottom: 1px solid var(--border-color);
}

.session-list-header h2 {
  font-size: 1.25rem;
  font-weight: 600;
}

.icon {
  width: 1.25rem;
  height: 1.25rem;
}

.session-items {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem;
}

.session-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem;
  border-radius: var(--radius);
  margin-bottom: 0.5rem;
  cursor: pointer;
  transition: background-color 0.2s;
}

.session-item:hover {
  background-color: var(--bg-color);
}

.session-item.active {
  background-color: var(--primary-color);
  color: white;
}

.delete-btn {
  background: none;
  padding: 0.25rem;
  opacity: 0.7;
  display: none;
}

.session-item:hover .delete-btn {
  display: block;
}

.session-item.active .delete-btn {
  color: white;
}

.delete-btn:hover {
  opacity: 1;
  background-color: rgba(0, 0, 0, 0.1);
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
}

.dialog h3 {
  margin-bottom: 1rem;
  font-size: 1.25rem;
}

.dialog input {
  width: 100%;
  margin-bottom: 1rem;
}

.dialog-buttons {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
</style>
