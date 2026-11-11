import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 独立管理后台包：不参与 Android / WebView 构建（架构 §6）。
export default defineConfig({
  // M8-10：复用现有 `/aozora-api/` 反代（不改 nginx）：页面由 FastAPI 的
  // StaticFiles 托管，浏览器地址栏含 `/aozora-api` 前缀，因此 base 必须带上它，
  // 否则 `/assets/*` 会 404。
  base: '/aozora-api/aozora-admin/',
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
  },
})
