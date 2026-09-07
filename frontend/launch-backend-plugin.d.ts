import type { Plugin } from 'vite';
/**
 * Vite plugin: launches the FastAPI backend (which in turn spawns the
 * decentralized robot fleet via FleetOrchestrator) as a child process
 * when `npm run dev` starts, and tears it down when Vite exits.
 *
 * Looks for a venv at ../backend/venv first (created via the one-time
 * setup in README). Falls back to system `python3`/`python` if no venv
 * is found, so this still works on a fresh machine with a global install.
 */
export declare function launchBackendPlugin(): Plugin;
