import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { launchBackendPlugin } from './launch-backend-plugin'

export default defineConfig({
  root: path.resolve(__dirname),
  plugins: [react(), launchBackendPlugin()],
})