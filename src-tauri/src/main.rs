#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use tao::dpi::LogicalSize;
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoop};
use tao::window::WindowBuilder;
use wry::{PageLoadEvent, WebContext, WebViewBuilder};

const FRONTEND_VERSION: &str = "workspace-20260601-telegram-ops";
const APP_URL: &str = "http://127.0.0.1:8000/?v=workspace-20260601-telegram-ops";
const BACKEND_HOST: &str = "127.0.0.1:8000";
const HEALTH_PATH: &str = "/health";
const LOG_DIR: &str = r"C:\Users\Public\AIManagerLogs";
const LOG_FILE: &str = "tauri-window-debug.log";
const SHARED_SITE_PACKAGES: &str = r"C:\Users\Public\AIManagerVenv\Lib\site-packages";

struct PythonCandidate {
    path: PathBuf,
    pythonpath: Option<&'static str>,
}

fn log_path() -> Option<PathBuf> {
    let dir = PathBuf::from(LOG_DIR);
    fs::create_dir_all(&dir).ok()?;
    Some(dir.join(LOG_FILE))
}

fn log(message: &str) {
    let _ = writeln!(io::stdout(), "{message}");

    if let Some(path) = log_path() {
        if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
            let _ = writeln!(file, "{message}");
        }
    }
}

fn browser_fallback_enabled() -> bool {
    std::env::var("AI_MANAGER_OPEN_BROWSER_FALLBACK")
        .map(|value| value == "1" || value.eq_ignore_ascii_case("true"))
        .unwrap_or(false)
}

fn open_browser_fallback_once() {
    if !browser_fallback_enabled() {
        log("AI MANAGER BROWSER FALLBACK DISABLED");
        return;
    }

    let marker = PathBuf::from(LOG_DIR).join("browser-fallback-opened.marker");
    if marker.exists() {
        log("AI MANAGER BROWSER FALLBACK ALREADY OPENED");
        return;
    }

    let _ = fs::write(&marker, APP_URL);
    log("AI MANAGER OPENING BROWSER FALLBACK ONCE");
    let _ = std::process::Command::new("cmd")
        .args(["/C", "start", "", APP_URL])
        .spawn();
}

fn backend_log_stdio(name: &str) -> Stdio {
    let path = PathBuf::from(LOG_DIR).join(name);
    match OpenOptions::new().create(true).append(true).open(path) {
        Ok(file) => Stdio::from(file),
        Err(_) => Stdio::null(),
    }
}

fn project_root() -> PathBuf {
    let current = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    if current.file_name().and_then(|name| name.to_str()) == Some("src-tauri") {
        current.parent().map(PathBuf::from).unwrap_or(current)
    } else {
        current
    }
}

fn backend_health_ok() -> bool {
    let mut stream = match TcpStream::connect(BACKEND_HOST) {
        Ok(stream) => stream,
        Err(_) => return false,
    };

    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));

    let request = format!(
        "GET {HEALTH_PATH} HTTP/1.1\r\nHost: {BACKEND_HOST}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }

    response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200")
}

fn backend_port_open() -> bool {
    TcpStream::connect(BACKEND_HOST).is_ok()
}

fn app_root_ok() -> bool {
    let mut stream = match TcpStream::connect(BACKEND_HOST) {
        Ok(stream) => stream,
        Err(_) => return false,
    };

    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));

    let request = format!("GET / HTTP/1.1\r\nHost: {BACKEND_HOST}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }

    response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200")
}

fn wait_backend_ready(timeout: Duration) -> bool {
    let started = Instant::now();
    while started.elapsed() < timeout {
        if backend_health_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(350));
    }
    false
}

fn start_backend_if_needed() -> Option<Child> {
    if backend_health_ok() {
        log("AI MANAGER BACKEND ALREADY READY");
        return None;
    }

    if backend_port_open() {
        log("AI MANAGER PORT 8000 IS OCCUPIED BY NON-FASTAPI SERVER; SKIPPING BACKEND START");
        return None;
    }

    let root = project_root();
    log(&format!(
        "AI MANAGER STARTING BACKEND: python -m src.main, cwd={}",
        root.display()
    ));

    let mut candidates: Vec<PythonCandidate> = Vec::new();
    if let Ok(local_app_data) = std::env::var("LOCALAPPDATA") {
        let python_root = PathBuf::from(local_app_data).join("Programs").join("Python");
        candidates.push(PythonCandidate {
            path: python_root.join("Python313").join("python.exe"),
            pythonpath: Some(SHARED_SITE_PACKAGES),
        });
        candidates.push(PythonCandidate {
            path: python_root.join("Python312").join("python.exe"),
            pythonpath: None,
        });
    }
    candidates.push(PythonCandidate {
        path: PathBuf::from("python"),
        pythonpath: None,
    });

    for candidate in candidates {
        let python = candidate.path;
        let is_path_candidate = python.components().count() > 1;
        if is_path_candidate && !python.exists() {
            continue;
        }

        log(&format!("AI MANAGER TRY PYTHON: {}", python.display()));
        let mut command = Command::new(&python);
        command
            .arg("-u")
            .arg("-m")
            .arg("src.main")
            .current_dir(&root)
            .stdout(backend_log_stdio("backend-runtime.out.log"))
            .stderr(backend_log_stdio("backend-runtime.err.log"));

        if let Some(pythonpath) = candidate.pythonpath {
            log(&format!("AI MANAGER PYTHONPATH: {pythonpath}"));
            command.env("PYTHONPATH", pythonpath);
        }

        match command.spawn() {
            Ok(child) => return Some(child),
            Err(error) => log(&format!(
                "AI MANAGER BACKEND START ERROR [{}]: {error}",
                python.display()
            )),
        }
    }

    None
}

fn backend_error_html() -> String {
    format!(
        r#"<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AI Manager backend is not ready</title>
  <style>
    body {{ margin:0; background:#050505; color:#fff; font-family:Segoe UI,Arial,sans-serif; }}
    main {{ max-width:760px; padding:40px; }}
    code, pre {{ background:#171717; color:#fca5a5; padding:3px 6px; border-radius:6px; }}
    pre {{ padding:14px; white-space:pre-wrap; }}
  </style>
</head>
<body>
  <main>
    <h1>AI Manager backend is not ready</h1>
    <p>Desktop window is working, but <code>{APP_URL}</code> is not the FastAPI backend.</p>
    <p>Stop any static server on port 8000, especially:</p>
    <pre>python -m http.server 8000</pre>
    <p>Then start the real backend:</p>
    <pre>python -m src.main</pre>
    <p>Expected health endpoint: <code>{APP_URL}health</code></p>
  </main>
</body>
</html>"#
    )
}

fn main() -> wry::Result<()> {
    log("AI MANAGER MAIN ENTERED");
    log(&format!(
        "AI MANAGER CURRENT DIR: {}",
        std::env::current_dir()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|e| format!("unknown: {e}"))
    ));
    log("AI MANAGER BEFORE TAO EVENT LOOP");

    let event_loop = EventLoop::new();
    log("AI MANAGER BEFORE NATIVE WINDOW CREATE");

    let window = WindowBuilder::new()
        .with_title("AI Manager")
        .with_inner_size(LogicalSize::new(1200.0, 800.0))
        .with_min_inner_size(LogicalSize::new(900.0, 650.0))
        .with_visible(true)
        .with_resizable(true)
        .build(&event_loop)
        .expect("failed to create native window");

    log("AI MANAGER AFTER NATIVE WINDOW CREATE");

    #[cfg(target_os = "windows")]
    {
        use tao::platform::windows::WindowExtWindows;
        log(&format!("AI MANAGER NATIVE WINDOW HWND: {:?}", window.hwnd()));
    }

    window.set_visible(true);
    window.set_focus();
    log("AI MANAGER AFTER SHOW FOCUS");

    let mut backend_child = start_backend_if_needed();
    let backend_ready = wait_backend_ready(Duration::from_secs(8));
    let root_ready = app_root_ok();
    log(&format!("AI MANAGER BACKEND READY: {backend_ready}"));
    log(&format!("AI MANAGER APP ROOT READY: {root_ready}"));

    log("AI MANAGER BEFORE WRY WEBVIEW CREATE");

    let data_dir = PathBuf::from(r"C:\Users\Public").join(format!("AIManagerWebView2-{FRONTEND_VERSION}"));
    if let Err(error) = std::fs::create_dir_all(&data_dir) {
        log(&format!("AI MANAGER WEBVIEW DATA DIR CREATE ERROR: {error}"));
    } else {
        log(&format!(
            "AI MANAGER WEBVIEW DATA DIR: {}",
            data_dir.display()
        ));
    }
    let mut web_context = WebContext::new(Some(data_dir));

    let mut webview_builder = WebViewBuilder::new_with_web_context(&mut web_context)
        .with_devtools(true)
        .with_initialization_script(
            "console.log('WRY INIT SCRIPT REACHED'); document.documentElement.style.background='black';",
        )
        .with_navigation_handler(|url| {
            log(&format!("AI MANAGER WRY NAVIGATION: {url}"));
            true
        })
        .with_on_page_load_handler(|event, url| {
            let event_name = match event {
                PageLoadEvent::Started => "Started",
                PageLoadEvent::Finished => "Finished",
            };
            log(&format!("AI MANAGER WRY PAGE LOAD: {event_name} {url}"));
        });

    webview_builder = if backend_ready || root_ready {
        webview_builder.with_url(APP_URL)
    } else {
        webview_builder.with_html(backend_error_html())
    };

    let webview = match webview_builder.build(&window)
    {
        Ok(webview) => {
            log("AI MANAGER AFTER WRY WEBVIEW CREATE");
            Some(webview)
        }
        Err(error) => {
            log(&format!("AI MANAGER WRY WEBVIEW CREATE ERROR: {error}"));
            log("AI MANAGER WEBVIEW2 RUNTIME IS MISSING OR BROKEN");
            open_browser_fallback_once();
            None
        }
    };

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;

        match event {
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            } => {
                log("AI MANAGER WINDOW CLOSE REQUESTED");
                if let Some(mut child) = backend_child.take() {
                    let _ = child.kill();
                }
                *control_flow = ControlFlow::Exit;
            }
            Event::WindowEvent {
                event: WindowEvent::Destroyed,
                ..
            } => {
                log("AI MANAGER WINDOW DESTROYED");
                if let Some(mut child) = backend_child.take() {
                    let _ = child.kill();
                }
                *control_flow = ControlFlow::Exit;
            }
            Event::WindowEvent {
                event: WindowEvent::Resized(_),
                ..
            } => {
                let _ = &webview;
            }
            _ => {}
        }
    });
}
