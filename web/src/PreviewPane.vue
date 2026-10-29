<script setup>
/** 预览区：当前页 A4 位图 + 翻页。图片刷新由外部 tick 变化驱动。 */
import { computed, ref, watch } from 'vue'
import { NButton, NPagination, NSpin } from 'naive-ui'

const props = defineProps({
  totalPages: { type: Number, default: 0 },
  tick: { type: Number, required: true },
})
const page = defineModel('page', { type: Number, required: true })

const loading = ref(true)
const failed = ref(false)

const src = computed(() =>
  props.totalPages > 0 ? `/api/page/${page.value}?v=${props.tick}` : '')

watch(src, () => {
  if (src.value) {
    loading.value = true
    failed.value = false
  }
})
watch(() => props.totalPages, (n) => {
  if (page.value >= n && n > 0) page.value = n - 1
})
</script>

<template>
  <div class="preview">
    <div v-if="totalPages <= 0" class="empty">
      <p>暂无可预览页面</p>
      <p class="hint">导入 Word，或在文本栏输入内容开始</p>
    </div>
    <template v-else>
      <div class="sheet-wrap">
        <n-spin :show="loading">
          <img class="page-sheet" :src="src" alt="预览页"
               @load="loading = false" @error="failed = true; loading = false" />
          <div v-if="failed" class="failed">本页渲染失败，请调整参数后重试</div>
        </n-spin>
      </div>
      <div class="pager">
        <n-pagination v-model:page="page" :page-count="totalPages" simple />
      </div>
    </template>
  </div>
</template>

<style scoped>
.preview {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #7a8471;
}
.empty .hint {
  font-size: 12px;
  color: #a3ab9c;
}
.sheet-wrap {
  flex: 1;
  overflow: auto;
  padding: 18px 0 8px;
  display: flex;
  justify-content: center;
}
.sheet-wrap :deep(.n-spin-container),
.sheet-wrap :deep(.n-spin-content) {
  width: 100%;
}
.pager {
  padding: 6px 0 12px;
  display: flex;
  justify-content: center;
}
.failed {
  padding: 40px;
  color: #c0564f;
  text-align: center;
}
</style>
