import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import lucideDirectImports from './vite-plugins/lucide-direct-imports.js'
import path from "path"
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

// https://vite.dev/config/
export default defineConfig({
  plugins: [lucideDirectImports(), react()],
  build: { reportCompressedSize: false, rollupOptions: { maxParallelFileOps: 8 } },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // The unauthenticated Vite proxy must not bypass the gateway boundary.
  // Use the built dashboard through secure_proxy.py for API-connected testing.
  server: { host: '127.0.0.1', strictPort: true, port: 5173, cors: false }
})
