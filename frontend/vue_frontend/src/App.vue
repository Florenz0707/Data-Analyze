<template>
  <n-config-provider :theme="theme">
    <n-global-style />
    <n-dialog-provider>
      <n-message-provider>
        <router-view />
      </n-message-provider>
    </n-dialog-provider>
  </n-config-provider>
</template>

<script setup>
import { computed } from 'vue';
import { NConfigProvider, NGlobalStyle, NMessageProvider, NDialogProvider, darkTheme } from 'naive-ui';
import { useAppStore } from './stores/app';

const appStore = useAppStore();

const theme = computed(() => (appStore.theme === 'dark' ? darkTheme : null));
</script>
