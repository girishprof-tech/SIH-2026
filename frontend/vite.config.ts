import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { launchBackendPlugin } from './launch-backend-plugin'

export default defineConfig({
  root: path.resolve(__dirname),
  plugins: [react(), launchBackendPlugin()],
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/three') || id.includes('node_modules/@react-three')) {
            return 'vendor-three'
          }
        },
      },
    },
  },
})