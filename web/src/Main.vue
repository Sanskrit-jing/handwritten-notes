<script setup>
/** 主界面：三栏布局——左参数 | 中文本编辑（常驻对照）| 右预览（主角）。 */
import { onMounted, ref, watch } from 'vue'
import {
  NBadge, NButton, NInput, NLayout,
  NLayoutContent, NLayoutSider, NPopconfirm, NUpload, useMessage,
} from 'naive-ui'
import { deleteBg, getMeta, postBg, postConfig, postDocx, postText } from './api.js'
import ParamPane from './ParamPane.vue'
import PreviewPane from './PreviewPane.vue'

const message = useMessage()

const meta = ref({ params: {}, enums: {}, fonts: [] })
const page = ref(0)
const tick = ref(1)          // 图片刷新时钟：任何会改变渲染的状态变化后自增
const editorText = ref('')
const dirty = ref(false)     // 文本已编辑未保存
const busy = ref(false)

onMounted(async () => {
  try {
    meta.value = await getMeta()
    editorText.value = meta.value.text ?? ''
    if (meta.value.total_pages > 0) page.value = 0
  } catch (e) {
    message.error(`加载失败：${e.message}`)
  }
})

function applyMeta(m, syncText = false) {
  meta.value = m
  if (syncText || !dirty.value) editorText.value = m.text ?? ''
  if (m.total_pages > 0 && page.value >= m.total_pages) page.value = m.total_pages - 1
  tick.value++
}

async function onParamChange(patch) {
  try {
    applyMeta(await postConfig(patch))
  } catch (e) {
    message.error(e.message)
  }
}

function onFontsUpdated(fonts) {
  meta.value.fonts = fonts
  message.success('字体已上传')
}

let saveTimer = null
let saving = false

function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(saveText, 800)
}

async function saveText() {
  if (saving) return // 上一笔请求未返回时跳过，由其完成后补发
  saving = true
  const snap = editorText.value
  try {
    applyMeta(await postText(snap), false)
    if (editorText.value === snap) {
      dirty.value = false
      message.success(`已更新：${meta.value.total_pages} 页 · ${meta.value.char_count} 字`)
    } else {
      dirty.value = true // 请求期间又有输入，不回填旧文本，稍后补发最新
      scheduleSave()
    }
  } catch (e) {
    message.error(e.message)
  } finally {
    saving = false
  }
}

watch(editorText, (v) => {
  if (v === meta.value.text) return // 载入/导入/保存回填不触发，仅响应真实编辑
  dirty.value = true
  scheduleSave()
})

async function onDocx({ file, onFinish, onError }) {
  try {
    busy.value = true
    dirty.value = false
    applyMeta(await postDocx(file.file), true)
    onFinish()
    message.success(`已导入：${meta.value.total_pages} 页 · ${meta.value.char_count} 字`)
  } catch (e) {
    message.error(e.message)
    onError()
  } finally {
    busy.value = false
  }
}

async function onBg({ file, onFinish, onError }) {
  try {
    const resp = await postBg(file.file)
    meta.value.params.bg_name = resp.bg_name
    tick.value++
    onFinish()
    message.success('背景已应用')
  } catch (e) {
    message.error(e.message)
    onError()
  }
}

async function onBgClear() {
  try {
    await deleteBg()
    meta.value.params.bg_name = ''
    tick.value++
  } catch (e) {
    message.error(e.message)
  }
}

function reshuffle() {
  onParamChange({ seed: (meta.value.params.seed ?? 0) + 1 })
  message.success('已换一版笔迹')
}

function exportFile(kind) {
  window.location.href = `/api/export/${kind}`
}
</script>

<template>
  <div class="root">
    <header class="topbar">
      <div class="brand">
        <span class="logo">手写记录</span>
        <span class="sub">Web 版</span>
      </div>
      <div class="actions">
        <n-upload :custom-request="onDocx" :show-file-list="false" accept=".docx">
          <n-button size="small" :loading="busy">导入 Word</n-button>
        </n-upload>
        <n-upload :custom-request="onBg" :show-file-list="false" accept=".png,.jpg,.jpeg,.webp">
          <n-button size="small">背景图片</n-button>
        </n-upload>
        <n-popconfirm v-if="meta.params?.bg_name" @positive-click="onBgClear">
          <template #trigger>
            <n-button size="small" quaternary>清除背景</n-button>
          </template>
          确认清除当前背景模板？
        </n-popconfirm>
        <n-button size="small" @click="reshuffle">换一版笔迹</n-button>
        <div class="spacer" />
        <n-button size="small" @click="exportFile('png')">导出 PNG</n-button>
        <n-button size="small" type="primary" @click="exportFile('pdf')">导出 PDF</n-button>
      </div>
    </header>
    <n-layout has-sider class="body">
      <n-layout-sider :width="340" bordered content-style="height: 100%; overflow: auto;">
        <div class="param-sider-bg">
          <ParamPane :meta="meta" @change="onParamChange" @fonts-updated="onFontsUpdated" />
        </div>
      </n-layout-sider>
      <n-layout-content content-style="height: 100%;">
      <div class="main-cols">
        <aside class="editor-col">
          <div class="editor-head">
            <span class="editor-title">文本</span>
            <span v-if="dirty" class="editor-dirty">未保存</span>
            <span class="editor-count">{{ editorText.length }} 字</span>
          </div>
          <div class="editor-body">
            <n-input v-model:value="editorText" type="textarea"
                     :autosize="false" style="height: 100%"
                     placeholder="每行一个段落，支持直接粘贴" />
          </div>
          <div class="editor-foot">
            <n-button size="small" :type="dirty ? 'warning' : 'primary'" @click="saveText">
              保存并重排
            </n-button>
          </div>
        </aside>
        <section class="preview-col">
          <div class="info-line">
            <template v-if="meta.total_pages">
              {{ meta.total_pages }} 页 · {{ meta.char_count }} 字
              <n-badge v-if="meta.params?.bg_name" :value="meta.params.bg_name" type="info"
                       :max-width="160" style="margin-left: 8px" />
            </template>
            <template v-else>未加载文档</template>
          </div>
          <PreviewPane v-model:page="page" :total-pages="meta.total_pages ?? 0" :tick="tick" />
        </section>
      </div>
      </n-layout-content>
    </n-layout>
  </div>
</template>

<style scoped>
.root {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.topbar {
  height: 48px;
  flex: none;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 16px;
  background: #fff;
  border-bottom: 1px solid #e4e7e1;
}
.brand {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.logo {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 1px;
}
.sub {
  font-size: 11px;
  color: #9aa394;
}
.actions {
  flex: 1;
  display: flex;
  gap: 8px;
  align-items: center;
}
.actions .n-upload {
  flex: none;
  width: auto;
}
.spacer {
  flex: 1;
}
.body {
  flex: 1;
  min-height: 0;
}
.param-sider-bg {
  min-height: 100%;
  background: #f5f7f3;
}
.info-line {
  padding: 6px 18px 0;
  font-size: 12px;
  color: #7a8471;
}
.main-cols {
  height: 100%;
  display: flex;
}
.editor-col {
  width: 360px;
  flex: none;
  border-right: 1px solid #efeff5;
  display: flex;
  flex-direction: column;
}
.preview-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #e9ece6;
}
.editor-head {
  padding: 6px 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.editor-title {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 1px;
}
.editor-dirty {
  font-size: 11px;
  color: #f0a020;
}
.editor-count {
  margin-left: auto;
  font-size: 12px;
  color: #a3ab9c;
}
.editor-body {
  flex: 1;
  min-height: 0;
  padding: 0 12px;
  display: flex;
}
.editor-foot {
  padding: 10px 14px;
  display: flex;
  justify-content: flex-end;
}
</style>
