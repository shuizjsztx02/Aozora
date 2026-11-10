import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 独立管理后台包：不参与 Android / WebView 构建（架构 §6）。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
  },
})
