// UNVERIFIED SCAFFOLD: written against the Tauri v2 API but NOT compiled in the environment where this project was
// built (no Rust toolchain). Expect to fix small API details on first `cargo tauri dev`.
//
// Responsibilities (deliberately tiny):
//   1. start the local Python engine (`python -m slcs serve`) bound to 127.0.0.1,
//   2. wait until it accepts connections,
//   3. open a window on that loopback URL,
//   4. stop the engine when the app exits.
// All search/indexing logic stays in the Python engine, where it is unit-tested.
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

struct Sidecar(Mutex<Option<Child>>);

fn wait_for_port(port: u16, timeout: Duration) -> Result<(), String> {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    Err(format!("search engine did not start on 127.0.0.1:{port}"))
}

fn main() {
    let port: u16 = std::env::var("SLCS_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(47615);
    let python = std::env::var("SLCS_PYTHON").unwrap_or_else(|_| "python".into());

    tauri::Builder::default()
        .setup(move |app| {
            let child = Command::new(&python)
                .args(["-m", "slcs", "serve", "--host", "127.0.0.1", "--port", &port.to_string()])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .map_err(|e| format!("cannot start '{python}': {e}"))?;
            app.manage(Sidecar(Mutex::new(Some(child))));
            wait_for_port(port, Duration::from_secs(20))?;
            let url = format!("http://127.0.0.1:{port}/").parse().map_err(|e| format!("{e}"))?;
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("Codebase Search")
                .inner_size(1280.0, 860.0)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(s) = app.try_state::<Sidecar>() {
                    if let Some(mut c) = s.0.lock().unwrap().take() {
                        let _ = c.kill();
                    }
                }
            }
        });
}
