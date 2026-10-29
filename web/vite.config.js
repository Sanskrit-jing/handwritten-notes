import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 开发联调：后端 HN_PORT 固定 8300（python server.py），生产构建后同源直连
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8300',
    },
  },
})
