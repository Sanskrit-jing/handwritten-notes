<script setup>
/** Provider 壳：主题 + 全局消息（window.$message 供非组件上下文使用）。 */
import { NConfigProvider, NDialogProvider, NMessageProvider, useMessage } from 'naive-ui'
import { defineComponent, h } from 'vue'
import Main from './Main.vue'

// 与桌面版暖绿纸感呼应的主题色
const overrides = {
  common: {
    primaryColor: '#5B7A5E',
    primaryColorHover: '#6B8A6E',
    primaryColorPressed: '#4C6A4F',
    borderRadius: '6px',
  },
}

const Bridge = defineComponent({
  setup() {
    window.$message = useMessage()
    return () => h(Main)
  },
})
</script>

<template>
  <n-config-provider :theme-overrides="overrides">
    <n-message-provider>
      <n-dialog-provider>
        <Bridge />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
