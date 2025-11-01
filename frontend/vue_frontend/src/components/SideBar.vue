<template>
  <div class="sidebar">
    <!-- Collapsed View -->
    <div v-if="props.isCollapsed" class="sidebar-collapsed">
      <div class="collapsed-top">
        <n-button text circle @click="emit('toggle-collapse')" class="sidebar-icon-btn">
          <template #icon><n-icon :component="MenuIcon" /></template>
        </n-button>
        <n-button circle @click="handleNewChat" class="sidebar-icon-btn new-chat-icon">
          <template #icon><n-icon :component="AddIcon" /></template>
        </n-button>
      </div>
      <div class="collapsed-bottom">
        <n-button text circle @click="handleLogout" class="sidebar-icon-btn logout-icon">
          <template #icon><n-icon :component="LogoutIcon" /></template>
        </n-button>
      </div>
    </div>

    <!-- Expanded View -->
    <div v-if="!props.isCollapsed" class="sidebar-expanded">
      <div class="header">
        <n-button text circle @click="emit('toggle-collapse')" class="sidebar-icon-btn">
          <template #icon><n-icon :component="MenuIcon" /></template>
        </n-button>
        <n-button @click="handleNewChat" :round="true" class="new-chat-btn">
          <template #icon><n-icon :component="AddIcon" /></template>
          New Chat
        </n-button>
      </div>
      <n-scrollbar class="session-list">
        <div
          v-for="session in chatStore.sessions"
          :key="session"
          class="session-item"
          :class="{ active: session === chatStore.currentSession }"
          @click="chatStore.setCurrentSession(session)"
        >
          <span class="session-name">{{ session }}</span>
          <n-button-group class="session-actions">
            <n-button text @click.stop="handleDelete(session)">
              <template #icon><n-icon :component="TrashIcon" /></template>
            </n-button>
          </n-button-group>
        </div>
      </n-scrollbar>
      
      <!-- Model Settings directy in sidebar -->
      <div class="model-settings">
        <p>Model Settings</p>
        <n-select
            v-model:value="modelStore.selectedProvider"
            :options="providerOptions"
            placeholder="Select Provider"
            :round="true"
        />
        <n-select
            v-if="isLocalProvider"
            v-model:value="modelStore.selectedModel"
            :options="modelOptions"
            placeholder="Select Model"
            class="model-select"
            :round="true"
        />
        <n-button @click="handleSelectModel" type="primary" block class="model-select" :round="true">Apply</n-button>
      </div>

      <div class="footer">
        <n-button @click="handleLogout" block type="error" :round="true" class="footer-btn">
          <template #icon><n-icon :component="LogoutIcon" /></template>
          Logout
        </n-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { NButton, NScrollbar, NButtonGroup, NIcon, useDialog, NSelect } from 'naive-ui';
import { 
    TrashOutline as TrashIcon, 
    LogOutOutline as LogoutIcon,
    AddOutline as AddIcon,
    MenuOutline as MenuIcon
} from '@vicons/ionicons5';
import { useChatStore } from '../stores/chat';
import { useAuthStore } from '../stores/auth';
import { useModelStore } from '../stores/model'; // Import model store
import { useRouter } from 'vue-router';

// Define props and emits for collapse functionality
const props = defineProps({
  isCollapsed: Boolean
});
const emit = defineEmits(['toggle-collapse']);

const chatStore = useChatStore();
const authStore = useAuthStore();
const modelStore = useModelStore(); // Initialize model store
const router = useRouter();
const dialog = useDialog();

// --- Model Selection Logic (Moved from ChatArea) ---
onMounted(() => {
    modelStore.fetchAll();
});

const providerOptions = computed(() => modelStore.providers.map(p => ({ label: p, value: p })));
const isLocalProvider = computed(() => ['transformers', 'ollama'].includes(modelStore.selectedProvider));
const modelOptions = computed(() => {
    if (modelStore.selectedProvider === 'transformers') {
        return modelStore.localModels.transformers.map(m => ({ label: m, value: m }));
    }
    if (modelStore.selectedProvider === 'ollama') {
        return modelStore.localModels.ollama.map(m => ({ label: m, value: m }));
    }
    return [];
});

const handleSelectModel = () => {
    modelStore.selectModel(modelStore.selectedProvider, modelStore.selectedModel);
};
// --- End Model Selection Logic ---

const handleNewChat = () => {
  const newSessionId = `session_${Date.now()}`;
  chatStore.createSession(newSessionId);
};

const handleDelete = (sessionId) => {
  dialog.create({ // Use .create for neutral, themed style
    title: 'Confirm Delete',
    content: `Are you sure you want to delete session "${sessionId}"?`,
    positiveText: 'Delete',
    negativeText: 'Cancel',
    positiveButtonProps: { type: 'error' }, // Keep delete button red
    onPositiveClick: () => {
      chatStore.deleteSession(sessionId);
    },
  });
};

const handleLogout = () => {
    dialog.create({ // Use .create for neutral, themed style
        title: 'Confirm Logout',
        content: 'Are you sure you want to log out?',
        positiveText: 'Logout',
        negativeText: 'Cancel',
        positiveButtonProps: { type: 'error' }, // Keep logout button red
        onPositiveClick: () => {
            authStore.clearApiKey();
            chatStore.clearUserChatData();
            router.push('/login');
        },
    });
};
</script>

<style scoped>
.sidebar {
  height: 100%;
  background-color: #f8fafd;
}

/* --- Collapsed Styles --- */
.sidebar-collapsed {
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
  /* Removed padding, added to top/bottom */
  justify-content: space-between; /* Pushes top and bottom apart */
}
.collapsed-top {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding-top: 12px; /* Added padding here */
}
.collapsed-bottom {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-bottom: 12px; /* Added padding here */
}
.sidebar-icon-btn {
  color: #5f6368; /* Neutral icon color */
}
.sidebar-icon-btn:hover {
  color: #4285f4; /* Brighter blue */
}
.new-chat-icon {
  /* Bright gradient button */
  background: linear-gradient(135deg, #4285f4, #9b59b6);
  color: white;
}
.new-chat-icon:hover {
  background: linear-gradient(135deg, #3a75d9, #8a4eae);
}
.logout-icon {
  color: #d9534f;
}

/* --- Expanded Styles --- */
.sidebar-expanded {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.header {
  padding: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.new-chat-btn {
  /* Bright gradient button */
  background: linear-gradient(135deg, #4285f4, #9b59b6);
  color: white;
  font-weight: 500;
  border: none;
  flex-grow: 1; /* Make button fill space */
}
.new-chat-btn:hover {
  background: linear-gradient(135deg, #3a75d9, #8a4eae);
}

.session-list {
  flex-grow: 1; /* Takes up available space */
  padding: 8px;
}
.session-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  cursor: pointer;
  border-radius: 9999px; /* Pill shape */
  margin-bottom: 4px;
}
.session-item:hover {
  background-color: #f0f2f5;
}
.session-item.active {
  background-color: #e8f0fe;
  color: #4285f4; /* Brighter blue */
  font-weight: 500;
}
.session-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-right: 8px;
}
.session-actions {
    opacity: 0; /* Hide by default */
}
.session-item:hover .session-actions,
.session-item.active .session-actions {
    opacity: 1; /* Show on hover/active */
}

/* Model Settings */
.model-settings {
  padding: 12px;
  border-top: 1px solid #eef0f2; /* Lighter border */
  border-bottom: 1px solid #eef0f2;
}
.model-settings p {
    margin: 0 0 8px;
    font-weight: 500;
    font-size: 0.9rem;
    color: #5f6368;
}
.model-select {
    margin-top: 8px;
}

.footer {
  padding: 12px;
  /* Removed border-top, now handled by model-settings */
}
.footer-btn {
    justify-content: flex-start; /* Align icon/text left */
}
</style>
