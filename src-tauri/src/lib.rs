use serde::{Deserialize, Serialize};
use std::{
    fs::{self, OpenOptions},
    io::{self, BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use tauri::{Emitter, Manager, RunEvent, State};

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Handshake {
    host: String,
    port: u16,
    token: String,
    data_dir: String,
}

#[derive(Default)]
struct BackendState {
    child: Option<Child>,
    runtime: Option<Handshake>,
    error: Option<String>,
}

#[derive(Clone, Default)]
struct BackendProcess {
    inner: Arc<Mutex<BackendState>>,
    stopping: Arc<AtomicBool>,
    supervisor_running: Arc<AtomicBool>,
}

fn runtime_snapshot(state: &BackendProcess) -> Result<Handshake, String> {
    let mut guard = state.inner.lock().map_err(|e| e.to_string())?;
    let alive = guard
        .child
        .as_mut()
        .is_some_and(|c| matches!(c.try_wait(), Ok(None)));
    if alive {
        if let Some(runtime) = guard.runtime.clone() {
            return Ok(runtime);
        }
    }
    Err(guard
        .error
        .clone()
        .unwrap_or_else(|| "后端正在启动，请稍后重试".into()))
}

#[tauri::command]
async fn handshake(state: State<'_, BackendProcess>) -> Result<Handshake, String> {
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let deadline = Instant::now() + Duration::from_secs(65);
        loop {
            match runtime_snapshot(&state) {
                Ok(hs) => return Ok(hs),
                Err(error) => {
                    if state.stopping.load(Ordering::SeqCst) || Instant::now() >= deadline {
                        return Err(error);
                    }
                }
            }
            thread::sleep(Duration::from_millis(100));
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn backend_runtime(state: State<'_, BackendProcess>) -> Result<Handshake, String> {
    handshake(state).await
}

#[tauri::command]
fn backend_running(state: State<'_, BackendProcess>) -> bool {
    runtime_snapshot(&state).is_ok()
}

#[tauri::command]
fn finish_splash(app: tauri::AppHandle) -> Result<(), String> {
    let main = app
        .get_webview_window("main")
        .ok_or_else(|| "找不到主窗口".to_string())?;
    main.show().map_err(|e| e.to_string())?;
    main.set_focus().map_err(|e| e.to_string())?;
    if let Some(splash) = app.get_webview_window("splashscreen") {
        let _ = splash.close();
    }
    Ok(())
}

#[tauri::command]
fn backend_log_path(app: tauri::AppHandle) -> Result<String, String> {
    Ok(app
        .path()
        .app_local_data_dir()
        .map_err(|e| e.to_string())?
        .join("logs/backend.log")
        .display()
        .to_string())
}

#[tauri::command]
fn restart_backend(app: tauri::AppHandle, state: State<'_, BackendProcess>) -> Result<(), String> {
    let exe = executable(&app)?;
    let data = app.path().app_local_data_dir().map_err(|e| e.to_string())?;
    fs::create_dir_all(data.join("logs")).map_err(|e| e.to_string())?;
    state.stopping.store(false, Ordering::SeqCst);
    let state = state.inner().clone();
    let child = {
        let mut guard = state.inner.lock().map_err(|e| e.to_string())?;
        guard.runtime = None;
        guard.error = Some("正在重启本地服务".into());
        guard.child.take()
    };
    if let Some(mut child) = child {
        stop_child(&mut child);
    }
    supervise(app, state, exe, data);
    Ok(())
}

#[tauri::command]
fn window_minimize(window: tauri::WebviewWindow) -> Result<(), String> {
    window.minimize().map_err(|e| e.to_string())
}

#[tauri::command]
fn window_toggle_maximize(window: tauri::WebviewWindow) -> Result<(), String> {
    if window.is_maximized().map_err(|e| e.to_string())? {
        window.unmaximize().map_err(|e| e.to_string())
    } else {
        window.maximize().map_err(|e| e.to_string())
    }
}

#[tauri::command]
fn window_close(app: tauri::AppHandle) {
    app.exit(0);
}

fn executable(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let name = if cfg!(windows) {
        "digivision-backend.exe"
    } else {
        "digivision-backend"
    };
    let bundled = app
        .path()
        .resource_dir()
        .map_err(|e| e.to_string())?
        .join("backend")
        .join(name);
    if bundled.is_file() {
        return Ok(bundled);
    }
    // 开发模式仍使用独立后端；发布版不回退到构建机源码目录。
    #[cfg(debug_assertions)]
    {
        let dev = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("resources/digivision-backend")
            .join(name);
        if dev.is_file() {
            return Ok(dev);
        }
    }
    Err(format!("找不到应用内后端：{}", bundled.display()))
}

fn stop_child(child: &mut Child) {
    // 关闭 stdin 请求 Python 正常关闭相机、模型和数据库。
    drop(child.stdin.take());
    let deadline = Instant::now() + Duration::from_secs(7);
    while Instant::now() < deadline {
        if !matches!(child.try_wait(), Ok(None)) {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn spawn_backend(
    exe: &Path,
    data: &Path,
    stopping: &AtomicBool,
) -> Result<(Child, Handshake), String> {
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(data.join("logs/backend.log"))
        .map_err(|e| e.to_string())?;
    let mut command = Command::new(exe);
    command
        .current_dir(data)
        .env("DIGIVISION_DATA_DIR", data)
        .env("DIGIVISION_MOCK_CAMERA", "0")
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.spawn().map_err(|e| format!("后端启动失败：{e}"))?;
    let stdout = child.stdout.take().ok_or("后端 stdout 不可用")?;
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let result = reader
            .read_line(&mut line)
            .map(|_| line)
            .map_err(|e| e.to_string());
        let _ = tx.send(result);
        let _ = io::copy(&mut reader, &mut io::sink());
    });
    let deadline = Instant::now() + Duration::from_secs(60);
    let result = loop {
        if stopping.load(Ordering::SeqCst) {
            break Err("应用正在退出".into());
        }
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(Ok(line)) => {
                break serde_json::from_str::<Handshake>(line.trim())
                    .map_err(|e| format!("后端握手无效：{e}"));
            }
            Ok(Err(e)) => break Err(e),
            Err(mpsc::RecvTimeoutError::Disconnected) => break Err("后端退出，未返回握手".into()),
            Err(mpsc::RecvTimeoutError::Timeout) => {
                if Instant::now() >= deadline {
                    break Err("后端启动超时，请检查 logs/backend.log".into());
                }
            }
        }
    };
    match result {
        Ok(hs) if hs.host == "127.0.0.1" && hs.port > 0 && !hs.token.is_empty() => Ok((child, hs)),
        other => {
            stop_child(&mut child);
            Err(other.err().unwrap_or_else(|| "后端握手字段无效".into()))
        }
    }
}

fn shutdown(state: &BackendProcess) {
    state.stopping.store(true, Ordering::SeqCst);
    if let Ok(mut guard) = state.inner.lock() {
        guard.runtime = None;
        if let Some(mut child) = guard.child.take() {
            stop_child(&mut child);
        }
    }
}

fn supervise(app: tauri::AppHandle, state: BackendProcess, exe: PathBuf, data: PathBuf) {
    if state.supervisor_running.swap(true, Ordering::SeqCst) {
        return;
    }
    thread::spawn(move || {
        let mut failures = 0usize;
        let mut healthy_since = Instant::now();
        loop {
            thread::sleep(Duration::from_millis(250));
            if state.stopping.load(Ordering::SeqCst) {
                break;
            }
            let alive = {
                let mut guard = state.inner.lock().unwrap();
                let alive = guard
                    .child
                    .as_mut()
                    .is_some_and(|child| matches!(child.try_wait(), Ok(None)));
                if !alive {
                    guard.child = None;
                    guard.runtime = None;
                    guard.error = Some("后端已退出，正在重新启动".into());
                }
                alive
            };
            if alive {
                if healthy_since.elapsed() > Duration::from_secs(30) {
                    failures = 0;
                }
                continue;
            }
            if failures >= 3 {
                let error = "后端连续启动失败，请检查数据目录 logs/backend.log";
                state.inner.lock().unwrap().error = Some(error.into());
                let _ = app.emit("python-failed", error);
                break;
            }
            let delay = [1, 2, 5][failures];
            failures += 1;
            for _ in 0..delay * 10 {
                if state.stopping.load(Ordering::SeqCst) {
                    break;
                }
                thread::sleep(Duration::from_millis(100));
            }
            match spawn_backend(&exe, &data, &state.stopping) {
                Ok((mut child, hs)) => {
                    let mut guard = state.inner.lock().unwrap();
                    if state.stopping.load(Ordering::SeqCst) {
                        stop_child(&mut child);
                        break;
                    }
                    guard.child = Some(child);
                    guard.runtime = Some(hs.clone());
                    guard.error = None;
                    healthy_since = Instant::now();
                    drop(guard);
                    let _ = app.emit("python-restarted", hs);
                }
                Err(error) => {
                    state.inner.lock().unwrap().error = Some(error);
                }
            }
        }
        state.supervisor_running.store(false, Ordering::SeqCst);
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(BackendProcess::default())
        .invoke_handler(tauri::generate_handler![
            handshake,
            backend_runtime,
            backend_running,
            finish_splash,
            backend_log_path,
            restart_backend,
            window_minimize,
            window_toggle_maximize,
            window_close
        ])
        .setup(|app| {
            let data = app.path().app_local_data_dir()?;
            fs::create_dir_all(data.join("logs"))?;
            let state = app.state::<BackendProcess>().inner().clone();
            match executable(app.handle()) {
                Ok(exe) => {
                    // 保持 UI 主线程可响应，前端握手在就绪前返回可重试错误。
                    supervise(app.handle().clone(), state, exe, data);
                }
                Err(error) => {
                    state.inner.lock().unwrap().error = Some(error);
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() == "main" && matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                window.app_handle().exit(0);
            }
        })
        .build(tauri::generate_context!())
        .expect("DigiVision 桌面应用初始化失败");
    app.run(|app, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            shutdown(&app.state::<BackendProcess>());
        }
    });
}
