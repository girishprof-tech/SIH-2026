import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
/**
 * Vite plugin: launches the FastAPI backend (which in turn spawns the
 * decentralized robot fleet via FleetOrchestrator) as a child process
 * when `npm run dev` starts, and tears it down when Vite exits.
 *
 * Looks for a venv at ../backend/venv first (created via the one-time
 * setup in README). Falls back to system `python3`/`python` if no venv
 * is found, so this still works on a fresh machine with a global install.
 */
export function launchBackendPlugin() {
    var backendProcess = null;
    var shuttingDown = false;
    var backendDir = path.resolve(__dirname, '../backend/backend');
    function resolvePython() {
        var isWin = process.platform === 'win32';
        var rootVenv = isWin
            ? path.resolve(__dirname, '../venv/Scripts/python.exe')
            : path.resolve(__dirname, '../venv/bin/python');
        if (existsSync(rootVenv))
            return rootVenv;
        var backendVenv = isWin
            ? path.resolve(__dirname, '../backend/venv/Scripts/python.exe')
            : path.resolve(__dirname, '../backend/venv/bin/python');
        if (existsSync(backendVenv))
            return backendVenv;
        console.warn('[launch-backend] No venv found at venv or backend/venv — falling back to system Python. ' +
            'Run the one-time backend setup in README.md if this fails.');
        return isWin ? 'python' : 'python3';
    }
    function shutdown() {
        if (shuttingDown || !backendProcess)
            return;
        shuttingDown = true;
        console.log('\n[launch-backend] Shutting down backend + robot fleet...');
        if (process.platform === 'win32') {
            if (backendProcess.pid) {
                try {
                    spawn('taskkill', ['/pid', backendProcess.pid.toString(), '/T', '/F'], { stdio: 'ignore' });
                }
                catch (_a) {
                    backendProcess.kill();
                }
            }
            else {
                backendProcess.kill();
            }
        }
        else {
            // uvicorn --reload spawns a child reloader process; killing the
            // whole process group (negative pid) ensures both die together.
            try {
                process.kill(-backendProcess.pid, 'SIGTERM');
            }
            catch (_b) {
                backendProcess.kill('SIGTERM');
            }
        }
    }
    return {
        name: 'launch-backend',
        configureServer: function (server) {
            var _a;
            if (process.env.SKIP_BACKEND === '1') {
                console.log('[launch-backend] SKIP_BACKEND=1 set — not launching backend.');
                return;
            }
            if (!existsSync(backendDir)) {
                console.warn("[launch-backend] Backend directory not found at ".concat(backendDir, ", skipping auto-launch."));
                return;
            }
            var pythonBin = resolvePython();
            var restartTimer = null;
            function cleanStalePort8000() {
                if (process.platform === 'win32') {
                    try {
                        var netstat = spawn('netstat', ['-ano', '-p', 'tcp'], { stdio: 'pipe' });
                        var out_1 = '';
                        netstat.stdout.on('data', function (d) { out_1 += d.toString(); });
                        netstat.on('close', function () {
                            for (var _i = 0, _a = out_1.split('\n'); _i < _a.length; _i++) {
                                var line = _a[_i];
                                if (line.includes(':8000') && line.includes('LISTENING')) {
                                    var parts = line.trim().split(/\s+/);
                                    var pid = parts[parts.length - 1];
                                    if (pid && pid !== '0') {
                                        console.log("[launch-backend] Freeing stale port 8000 (PID ".concat(pid, ")..."));
                                        spawn('taskkill', ['/pid', pid, '/F'], { stdio: 'ignore' });
                                    }
                                }
                            }
                        });
                    }
                    catch (_a) {
                        // ignore
                    }
                }
            }
            function startBackend() {
                if (shuttingDown)
                    return;
                cleanStalePort8000();
                console.log("[launch-backend] Starting FastAPI backend + robot fleet (".concat(pythonBin, ")..."));
                backendProcess = spawn(pythonBin, ['-m', 'uvicorn', 'app.main:app', '--reload', '--host', '0.0.0.0', '--port', '8000'], {
                    cwd: backendDir,
                    stdio: 'inherit',
                    env: process.env,
                    detached: process.platform !== 'win32',
                });
                backendProcess.on('exit', function (code) {
                    if (!shuttingDown) {
                        console.warn("[launch-backend] Backend process exited (code ".concat(code, "). Auto-restarting in 2s..."));
                        if (restartTimer)
                            clearTimeout(restartTimer);
                        restartTimer = setTimeout(startBackend, 2000);
                    }
                });
            }
            startBackend();
            process.on('SIGINT', function () {
                shutdown();
                process.exit();
            });
            process.on('SIGTERM', function () {
                shutdown();
                process.exit();
            });
            process.on('exit', shutdown);
            (_a = server.httpServer) === null || _a === void 0 ? void 0 : _a.on('close', shutdown);
        },
    };
}
