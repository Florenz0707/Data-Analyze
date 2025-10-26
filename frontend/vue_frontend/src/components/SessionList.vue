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
          @click.stop="promptDelete(session)"
          title="删除会话"
        >
          <trash-icon class="icon" />
        </button>
      </div>
    </div>

    <!-- 新建会话对话框 -->
    <div v-if="showNewSessionDialog" class="dialog-overlay" @click.self="showNewSessionDialog = false">
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
          <button class="primary" @click="createSession" :disabled="!newSessionName.trim()">创建</button>
        </div>
      </div>
    </div>

    <!-- (新) 删除确认对话框 -->
    <div v-if="showDeleteConfirm" class="dialog-overlay" @click.self="showDeleteConfirm = false">
      <div class="dialog">
        <h3>确认删除</h3>
        <p>确定要删除会话 "<strong>{{ sessionToDelete }}</strong>" 吗？此操作无法撤销。</p>
        <div class="dialog-buttons">
          <button class="secondary" @click="showDeleteConfirm = false">取消</button>
          <button class="danger" @click="confirmDelete">删除</button>
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

// (新) 删除确认状态
const showDeleteConfirm = ref(false);
const sessionToDelete = ref(null);

const selectSession = (session) => {
  emits('select', session);
};

// (新) 步骤1: 弹出确认框
const promptDelete = (session) => {
  sessionToDelete.value = session;
  showDeleteConfirm.value = true;
};

// (新) 步骤2: 确认删除
const confirmDelete = () => {
  if (sessionToDelete.value) {
    emits('delete', sessionToDelete.value);
  }
  showDeleteConfirm.value = false;
  sessionToDelete.value = null;
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

/* 对话框通用样式 */
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
