import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 独立管理后台包：不参与 Android / WebView 构建（架构 §6）。
export default defineConfig({
  // M8-10：与 `/aozora-api/` 同端口发布（A 机 nginx 多业务共享，无独立域名）。
  // 子路径部署必须设 base，否则 `/assets/*` 会 404。
  base: '/aozora-admin/',
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
  },
})
