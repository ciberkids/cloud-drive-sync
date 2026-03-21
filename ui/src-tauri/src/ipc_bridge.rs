use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixStream;
use tokio::sync::{mpsc, Mutex, oneshot};
use std::collections::HashMap;

static REQUEST_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Debug, Serialize, Deserialize)]
struct JsonRpcRequest {
    jsonrpc: String,
    id: u64,
    method: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    params: Option<Value>,
}

#[derive(Debug, Serialize, Deserialize)]
struct JsonRpcResponse {
    jsonrpc: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    id: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<JsonRpcError>,
    #[serde(skip_serializing_if = "Option::is_none")]
    method: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    params: Option<Value>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct JsonRpcError {
    pub code: i64,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

impl std::fmt::Display for JsonRpcError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "RPC error {}: {}", self.code, self.message)
    }
}

type PendingRequests = Arc<Mutex<HashMap<u64, oneshot::Sender<Result<Value, JsonRpcError>>>>>;

#[derive(Clone)]
pub struct DaemonBridge {
    sender: mpsc::Sender<String>,
    pending: PendingRequests,
    notification_tx: mpsc::Sender<(String, Value)>,
    connected: Arc<std::sync::atomic::AtomicBool>,
}

impl DaemonBridge {
    pub fn new(notification_tx: mpsc::Sender<(String, Value)>) -> Self {
        let (sender, _) = mpsc::channel(1);
        Self {
            sender,
            pending: Arc::new(Mutex::new(HashMap::new())),
            notification_tx,
            connected: Arc::new(std::sync::atomic::AtomicBool::new(false)),
        }
    }

    pub async fn connect(&mut self) -> Result<(), String> {
        let socket_path = get_socket_path();

        let stream = UnixStream::connect(&socket_path)
            .await
            .map_err(|e| format!("Failed to connect to daemon at {}: {}", socket_path, e))?;

        let (reader, writer) = stream.into_split();
        let (tx, mut rx) = mpsc::channel::<String>(256);
        self.sender = tx;
        self.connected.store(true, Ordering::SeqCst);

        let pending = self.pending.clone();
        let notification_tx = self.notification_tx.clone();
        let connected = self.connected.clone();

        // Writer task
        let mut writer = writer;
        tokio::spawn(async move {
            while let Some(msg) = rx.recv().await {
                let line = format!("{}\n", msg);
                if writer.write_all(line.as_bytes()).await.is_err() {
                    break;
                }
            }
        });

        // Reader task
        tokio::spawn(async move {
            let mut buf_reader = BufReader::new(reader);
            let mut line = String::new();

            loop {
                line.clear();
                match buf_reader.read_line(&mut line).await {
                    Ok(0) | Err(_) => {
                        connected.store(false, Ordering::SeqCst);
                        // Clean up pending requests
                        let mut pending_map = pending.lock().await;
                        for (_, sender) in pending_map.drain() {
                            let _ = sender.send(Err(JsonRpcError {
                                code: -1,
                                message: "Connection lost".to_string(),
                                data: None,
                            }));
                        }
                        break;
                    }
                    Ok(_) => {
                        let trimmed = line.trim();
                        if trimmed.is_empty() {
                            continue;
                        }

                        if let Ok(resp) = serde_json::from_str::<JsonRpcResponse>(trimmed) {
                            // Check if it's a notification (no id)
                            if resp.id.is_none() {
                                if let Some(method) = resp.method {
                                    let params = resp.params.unwrap_or(Value::Null);
                                    let _ = notification_tx.send((method, params)).await;
                                }
                                continue;
                            }

                            if let Some(id) = resp.id {
                                let mut pending_map = pending.lock().await;
                                if let Some(sender) = pending_map.remove(&id) {
                                    if let Some(error) = resp.error {
                                        let _ = sender.send(Err(error));
                                    } else {
                                        let _ = sender.send(Ok(
                                            resp.result.unwrap_or(Value::Null),
                                        ));
                                    }
                                }
                            }
                        }
                    }
                }
            }
        });

        Ok(())
    }

    pub fn is_connected(&self) -> bool {
        self.connected.load(Ordering::SeqCst)
    }

    pub async fn call(
        &self,
        method: &str,
        params: Option<Value>,
    ) -> Result<Value, String> {
        if !self.is_connected() {
            return Err("Not connected to daemon".to_string());
        }

        let id = REQUEST_ID.fetch_add(1, Ordering::SeqCst);
        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id,
            method: method.to_string(),
            params,
        };

        let (tx, rx) = oneshot::channel();

        {
            let mut pending = self.pending.lock().await;
            pending.insert(id, tx);
        }

        let msg = serde_json::to_string(&request).map_err(|e| e.to_string())?;
        self.sender
            .send(msg)
            .await
            .map_err(|e| format!("Failed to send: {}", e))?;

        match tokio::time::timeout(std::time::Duration::from_secs(30), rx).await {
            Ok(Ok(Ok(value))) => Ok(value),
            Ok(Ok(Err(rpc_error))) => Err(rpc_error.to_string()),
            Ok(Err(_)) => Err("Request cancelled".to_string()),
            Err(_) => {
                let mut pending = self.pending.lock().await;
                pending.remove(&id);
                Err("Request timed out".to_string())
            }
        }
    }
}

fn get_socket_path() -> String {
    if let Ok(runtime_dir) = std::env::var("XDG_RUNTIME_DIR") {
        format!("{}/cloud-drive-sync.sock", runtime_dir)
    } else {
        let uid = unsafe { libc::getuid() };
        format!("/run/user/{}/cloud-drive-sync.sock", uid)
    }
}
