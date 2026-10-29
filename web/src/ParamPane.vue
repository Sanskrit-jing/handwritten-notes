<script setup>
/** 右栏参数面板：本地副本编辑，防抖 400ms 后把受管字段整体上抛。 */
import { reactive, watch } from 'vue'
import {
  NButton, NCollapse, NCollapseItem, NForm, NFormItem, NInputNumber,
  NSelect, NSlider, NUpload,
} from 'naive-ui'
import { postFont } from './api.js'

const props = defineProps({
  meta: { type: Object, required: true },
})
const emit = defineEmits(['change', 'fonts-updated'])

// 本面板管理的字段（与服务端 _LAYOUT_FIELDS/_RENDER_FIELDS 对应）
const FIELDS = [
  'font_path', 'font_size_pt', 'line_factor',
  'margin_top_mm', 'margin_bottom_mm', 'margin_left_mm', 'margin_right_mm',
  'paper', 'error_rate', 'rewrite_mode', 'strike_mode', 'ink_color',
  'stroke_weight', 'dx_amp', 'dy_amp', 'rot_amp',
]
const local = reactive(Object.fromEntries(FIELDS.map(k => [k, null])))
let filled = false

watch(() => props.meta, (m) => {
  if (!m?.params) return
  for (const k of FIELDS) local[k] = m.params[k]
  filled = true
}, { immediate: true, deep: true })

let timer = null
watch(local, () => {
  if (!filled) return
  clearTimeout(timer)
  timer = setTimeout(() => {
    emit('change', Object.fromEntries(FIELDS.map(k => [k, local[k]])))
  }, 400)
}, { deep: true })

const fontOptions = () => (props.meta?.fonts ?? [])
  .map(([name, path]) => ({ label: name, value: path }))

async function onFontUpload({ file, onFinish, onError }) {
  try {
    const resp = await postFont(file.file)
    emit('fonts-updated', resp.fonts)
    onFinish()
  } catch (e) {
    window.$message?.error(e.message)
    onError()
  }
}
</script>

<template>
  <n-form label-placement="left" label-width="96" size="small" class="param-pane"
          :show-feedback="false">
    <n-collapse :default-expanded-names="['font', 'paper']">
      <n-collapse-item name="font">
        <template #header><span class="gdot" style="background: #18a058" />字体字号</template>
        <n-form-item label="字体">
          <n-select v-model:value="local.font_path" :options="fontOptions()"
                    filterable tag size="tiny" />
          <n-upload :custom-request="onFontUpload" :show-upload-list="false"
                    accept=".ttf,.ttc,.otf">
            <n-button size="tiny" style="margin-left: 4px">上传</n-button>
          </n-upload>
        </n-form-item>
        <n-form-item label="字号 (pt)">
          <n-input-number v-model:value="local.font_size_pt" :min="6" :max="48"
                          :step="0.5" style="width: 100%" />
        </n-form-item>
      </n-collapse-item>

      <n-collapse-item name="paper">
        <template #header><span class="gdot" style="background: #2080f0" />纸张与版式</template>
        <n-form-item label="纸张样式">
          <n-select v-model:value="local.paper"
                    :options="(meta.enums?.paper ?? []).map(v => ({ label: v, value: v }))" />
        </n-form-item>
        <n-form-item label="页边距 (mm)">
          <div class="margins">
            <n-input-number v-model:value="local.margin_top_mm" :min="5" :max="60" />
            <n-input-number v-model:value="local.margin_bottom_mm" :min="5" :max="60" />
            <n-input-number v-model:value="local.margin_left_mm" :min="5" :max="60" />
            <n-input-number v-model:value="local.margin_right_mm" :min="5" :max="60" />
          </div>
        </n-form-item>
        <n-form-item :label="`行距 ×${(local.line_factor ?? 0).toFixed(2)}`">
          <n-slider v-model:value="local.line_factor" :min="1.1" :max="2.6"
                    :step="0.05" />
        </n-form-item>
      </n-collapse-item>

      <n-collapse-item name="real">
        <template #header><span class="gdot" style="background: #f0a020" />真实感 · 抖动与涂改</template>
        <n-form-item :label="`错字率 ${(100 * (local.error_rate ?? 0)).toFixed(1)}%`">
          <n-slider v-model:value="local.error_rate" :min="0" :max="0.05" :step="0.005" />
        </n-form-item>
        <n-form-item label="错字重写">
          <n-select v-model:value="local.rewrite_mode"
                    :options="(meta.enums?.rewrite_mode ?? []).map(v => ({ label: v, value: v }))" />
        </n-form-item>
        <n-form-item label="涂改样式">
          <n-select v-model:value="local.strike_mode"
                    :options="(meta.enums?.strike_mode ?? []).map(v => ({ label: v, value: v }))" />
        </n-form-item>
        <n-form-item :label="`水平位移 ${local.dx_amp ?? 0} px`">
          <n-slider v-model:value="local.dx_amp" :min="0" :max="10" :step="0.5" />
        </n-form-item>
        <n-form-item :label="`竖直位移 ${local.dy_amp ?? 0} px`">
          <n-slider v-model:value="local.dy_amp" :min="0" :max="10" :step="0.5" />
        </n-form-item>
        <n-form-item :label="`旋转 ${((local.rot_amp ?? 0) * 100).toFixed(0)}`">
          <n-slider v-model:value="local.rot_amp" :min="0" :max="0.2" :step="0.01" />
        </n-form-item>
      </n-collapse-item>

      <n-collapse-item name="ink">
        <template #header><span class="gdot" style="background: #8a6fe8" />墨迹</template>
        <n-form-item label="墨色">
          <n-select v-model:value="local.ink_color"
                    :options="Object.keys(meta.enums?.ink_color ?? {})
                      .map(v => ({ label: v, value: v }))" />
        </n-form-item>
        <n-form-item :label="`笔迹粗细 ×${(local.stroke_weight ?? 0).toFixed(2)}`">
          <n-slider v-model:value="local.stroke_weight" :min="0.5" :max="2.5"
                    :step="0.05" />
        </n-form-item>
      </n-collapse-item>
    </n-collapse>
  </n-form>
</template>

<style scoped>
.param-pane {
  padding: 12px 12px 24px;
}
/* 折叠组卡片化：白底圆角卡片浮于浅灰工作台底色上 */
.param-pane :deep(.n-collapse-item) {
  background: #fff;
  border: 1px solid #e3e7e0;
  border-radius: 8px;
  margin-bottom: 10px;
  box-shadow: 0 1px 3px rgba(31, 41, 34, 0.05);
  overflow: hidden;
}
.param-pane :deep(.n-collapse-item__header) {
  font-weight: 600;
  padding: 10px 14px;
}
.param-pane :deep(.n-collapse-item__header-main) {
  display: flex;
  align-items: center;
}
.gdot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 8px;
  flex: none;
}
.param-pane :deep(.n-collapse-item__content-inner) {
  padding: 0 14px 12px;
}
.param-pane :deep(.n-form-item) {
  margin-bottom: 8px;
}
.param-pane :deep(.n-form-item:last-child) {
  margin-bottom: 4px;
}
.margins {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px;
  width: 100%;
}
.margins .n-input-number {
  width: 100%;
}
</style>
