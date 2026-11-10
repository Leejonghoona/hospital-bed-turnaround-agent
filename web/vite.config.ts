import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 개발 중에는 /api를 FastAPI(8000)로 넘긴다. 배포는 FastAPI가 빌드된 화면(dist)을 함께 내준다.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8000' } },
})
