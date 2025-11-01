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
        <!-- 修改：添加一个带管理按钮的头部 -->
        <div class="model-settings-header">
          <p>Model Settings</p>
          <n-button text @click="showManageModelsModal = true" title="Manage Custom Models">
            <template #icon><n-icon :component="SettingsIcon" /></template>
          </n-button>
        </div>
        
        <!-- 
          修改 providerOptions 以使用分组
          并添加 :filterable 使其可搜索
        -->
        <n-select
            v-model:value="modelStore.selectedProvider"
            :options="providerOptions"
            placeholder="Select Provider"
            :round="true"
            :filterable="true" 
        />
        <n-select
            v-if="isLocalProvider"
            v-model:value="modelStore.selectedModel"
            :options="modelOptions"
            placeholder="Select Model"
            class="model-select"
            :round="true"
            :filterable="true"
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

    <!-- 新增：管理自定义模型的模态框 -->
    <n-modal 
      v-model:show="showManageModelsModal" 
      title="Manage Custom Models" 
      preset="card" 
      style="width: 600px;"
      :mask-closable="false"
    >
      <n-tabs type="line" animated>
        <n-tab-pane name="list" tab="My Models">
          <n-list bordered>
            <n-list-item v-for="model in modelStore.customModels" :key="model">
              <span class="custom-model-name">{{ model }}</span>
              <template #suffix>
                <n-button text type="error" @click="handleDeleteCustomModel(model)" title="Delete Model">
                  <template #icon><n-icon :component="TrashIcon" /></template>
                </n-button>
              </template>
            </n-list-item>
            <template #footer v-if="modelStore.customModels.length === 0">
              You haven't added any custom models yet.
            </template>
          </n-list>
        </n-tab-pane>
        <n-tab-pane name="add" tab="Add New Model">
          <n-form ref="addModelFormRef" :model="addModelData" :rules="addModelFormRules" label-placement="top">
            <n-form-item label="Model Name (Alias)" path="alias">
              <n-input v-model:value="addModelData.alias" placeholder="e.g., My Llama 3 (This is the name you'll see)" />
            </n-form-item>
            <n-form-item label="Model Name (on Provider)" path="model_name">
              <n-input v-model:value="addModelData.model_name" placeholder="e.g., llama3:latest or gpt-4o" />
            </n-form-item>
            <n-form-item label="Base URL" path="base_url">
              <n-input v-model:value="addModelData.base_url" placeholder="e.g., http://localhost:11434/v1" />
            </n-form-item>
            <n-form-item label="API Key" path="api_key">
              <n-input 
                v-model:value="addModelData.api_key" 
                type="password" 
                show-password-on="click" 
                placeholder="Required by most providers (e.g., OpenAI)" 
              />
            </n-form-item>
          </n-form>
          <n-button type="primary" block @click="handleSaveCustomModel" :loading="isSavingModel">Save Model</n-button>
        </n-tab-pane>
      </n-tabs>
    </n-modal>

  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'; // 新增 ref
import { 
  NButton, NScrollbar, NButtonGroup, NIcon, useDialog, NSelect,
  NModal, NTabs, NTabPane, NList, NListItem, NForm, NFormItem, NInput, useMessage // 新增
} from 'naive-ui';
import { 
    TrashOutline as TrashIcon, 
    LogOutOutline as LogoutIcon,
    AddOutline as AddIcon,
    MenuOutline as MenuIcon,
    SettingsOutline as SettingsIcon // 新增
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
const message = useMessage(); // 新增

// --- Model Selection Logic (Moved from ChatArea) ---
onMounted(() => {
    modelStore.fetchAll();
});

// *** 修改 providerOptions ***
// 使用分组来区分标准提供方和自定义模型
const providerOptions = computed(() => {
  const providerGroup = {
    type: 'group',
    label: 'Providers',
    children: modelStore.providers.map(p => ({ label: p, value: p }))
  };

  const customGroup = {
    type: 'group',
    label: 'Custom Models',
    children: modelStore.customModels.map(m => ({ label: m, value: m }))
  };

  const options = [];
  if (providerGroup.children.length > 0) {
    options.push(providerGroup);
  }
  if (customGroup.children.length > 0) {
    options.push(customGroup);
  }
  return options;
});


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
    // 当选择的是自定义模型时，modelStore.selectedModel 会是 null（因为 isLocalProvider 为 false）
    // 这符合 /api/llm/select 接口对于远程/自定义提供方的预期
    modelStore.selectModel(modelStore.selectedProvider, modelStore.selectedModel);
};
// --- End Model Selection Logic ---

// --- 新增：自定义模型管理 ---

const showManageModelsModal = ref(false);
const isSavingModel = ref(false);
const addModelFormRef = ref(null);
const addModelData = ref({
  base_url: '',
  model_name: '',
  api_key: '',
  alias: ''
});

// 根据 openapi.yaml, base_url, model_name, 和 api_key 是必需的
// 但为了灵活性 (例如 Ollama 不需要 key), 我们只在前端强制要求 alias, model_name 和 base_url
const addModelFormRules = {
  alias: { required: true, message: "A unique Model Name (Alias) is required", trigger: 'blur' },
  model_name: { required: true, message: 'Model Name (on provider) is required', trigger: 'blur' },
  base_url: { required: true, message: 'Base URL is required', trigger: 'blur' }
};

const resetAddModelForm = () => {
  addModelData.value = { base_url: '', model_name: '', api_key: '', alias: '' };
};

const handleSaveCustomModel = () => {
  addModelFormRef.value?.validate(async (errors) => {
    if (!errors) {
      isSavingModel.value = true;
      try {
        await modelStore.addCustomModel(addModelData.value);
        message.success('Model added successfully!');
        resetAddModelForm();
        // 保持模态框打开，以便用户可以切换到列表查看或添加另一个
      } catch (error) {
        message.error('Failed to add model. See console for details.');
      } finally {
        isSavingModel.value = false;
      }
    } else {
      message.error('Please check the form for errors.');
    }
  });
};

const handleDeleteCustomModel = (modelName) => {
  dialog.warning({ // 使用 warning 级别的对话框
    title: 'Confirm Delete',
    content: `Are you sure you want to delete the custom model "${modelName}"? This action cannot be undone.`,
    positiveText: 'Delete',
    negativeText: 'Cancel',
    positiveButtonProps: { type: 'error' },
    onPositiveClick: () => {
      modelStore.deleteCustomModel(modelName);
      message.success(`Model "${modelName}" deleted.`);
    },
  });
};

// --- 结束：自定义模型管理 ---


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
/* 新增：模型设置头部样式 */
.model-settings-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px; /* 保持与原 <p> 相同的间距 */
}

.model-settings-header p {
  margin: 0; /* 移除 <p> 标签的默认 margin */
  font-weight: 500;
  font-size: 0.9rem;
  color: #5f6368;
}

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

/* 新增：自定义模型列表项样式 */
.custom-model-name {
  font-weight: 500;
}
</style>

