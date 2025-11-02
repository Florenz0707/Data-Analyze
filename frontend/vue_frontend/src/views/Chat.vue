<template>
  <div class="chat-view">
    <n-layout has-sider class="chat-layout">
      <n-layout-sider
        :width="260"
        :collapsed-width="64"
        :collapsed="isCollapsed"
        collapse-mode="width"
        class="chat-sidebar"
      >
        <SideBar :is-collapsed="isCollapsed" @toggle-collapse="isCollapsed = !isCollapsed" />
      </n-layout-sider>
      <n-layout-content class="chat-content">
        <ChatArea />
      </n-layout-content>
    </n-layout>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue';
import { NLayout, NLayoutSider, NLayoutContent } from 'naive-ui';
import { useChatStore } from '../stores/chat';
import SideBar from '../components/SideBar.vue';
import ChatArea from '../components/ChatArea.vue';

const chatStore = useChatStore();
const isCollapsed = ref(false);

onMounted(() => {
  chatStore.initialize();
});
</script>

<style scoped>
.chat-view, .chat-layout {
  height: 100vh;
  background-color: #f8fafd; /* Bright, light background */
}
.chat-sidebar {
  background-color: #f8fafd;
  border-right: 1px solid #eef0f2; /* Lighter border */
  transition: width 0.3s ease;
}
.chat-content {
  background-color: #ffffff; /* White content background */
}
</style>
