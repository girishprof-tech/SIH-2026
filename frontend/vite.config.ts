import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { launchBackendPlugin } from './launch-backend-plugin'

export default defineConfig({ plugins: [react(), launchBackendPlugin()] })