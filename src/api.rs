//! Vikunja 数据层：ureq + rustls（不用 openssl），断网不崩。
use crate::parse::{shanghai_offset};
use chrono::{DateTime, FixedOffset, TimeZone};
use serde_json::{json, Value};
use std::time::Duration;

pub struct Vikunja {
    pub base: String,
    user: String,
    password: String,
    pub project: i64,
    token: Option<String>,
    pub last_error: String,
    agent: ureq::Agent,
}

impl Vikunja {
    pub fn new(cfg: &std::collections::BTreeMap<String, String>) -> Self {
        let base = cfg.get("VIKUNJA_URL").cloned().unwrap_or_default().trim_end_matches('/').to_string();
        let project: i64 = cfg
            .get("VIKUNJA_PROJECT")
            .and_then(|s| s.trim().parse().ok())
            .unwrap_or(2);
        Self {
            base,
            user: cfg.get("VIKUNJA_USER").cloned().unwrap_or_default(),
            password: cfg.get("VIKUNJA_PASS").cloned().unwrap_or_default(),
            project,
            token: None,
            last_error: String::new(),
            agent: ureq::AgentBuilder::new().timeout(Duration::from_secs(15)).build(),
        }
    }

    fn call(&self, method: &str, path: &str, body: Option<&Value>) -> Result<Value, String> {
        let url = format!("{}{}", self.base, path);
        let auth = self
            .token
            .as_ref()
            .map(|t| format!("Bearer {}", t))
            .unwrap_or_default();
        let resp = match (method, body) {
            ("GET", _) => self.agent.get(&url).set("Authorization", &auth).call(),
            ("PUT", Some(b)) => self.agent.put(&url).set("Authorization", &auth).send_json(b.clone()),
            ("POST", Some(b)) => self.agent.post(&url).set("Authorization", &auth).send_json(b.clone()),
            _ => return Err("不支持的方法".into()),
        };
        match resp {
            Ok(r) => r.into_json::<Value>().map_err(|e| e.to_string()),
            Err(ureq::Error::Status(code, r)) => {
                let text = r.into_string().unwrap_or_default();
                Err(format!("HTTP {code}: {}", text.chars().take(200).collect::<String>()))
            }
            Err(e) => Err(e.to_string()),
        }
    }

    fn authed_call(
        &mut self,
        method: &str,
        path: &str,
        body: Option<&Value>,
    ) -> Result<Value, String> {
        if self.token.is_none() && !self.login() {
            return Err(self.last_error.clone());
        }
        let url_path = path.to_string();
        match self.call(method, &url_path, body) {
            Ok(v) => Ok(v),
            Err(e) if e.starts_with("HTTP 401") || e.starts_with("HTTP 403") => {
                // token 过期 → 重登一次再试
                self.token = None;
                if self.login() {
                    self.call(method, &url_path, body)
                } else {
                    Err(self.last_error.clone())
                }
            }
            Err(e) => Err(e),
        }
    }

    pub fn login(&mut self) -> bool {
        if self.base.is_empty() {
            self.last_error = "缺少 VIKUNJA_URL".into();
            return false;
        }
        match self.call(
            "POST",
            "/api/v1/login",
            Some(&json!({"username": self.user, "password": self.password})),
        ) {
            Ok(v) => match v.get("token").and_then(|t| t.as_str()) {
                Some(t) => {
                    self.token = Some(t.to_string());
                    self.last_error.clear();
                    true
                }
                None => {
                    self.last_error = "登录响应缺少 token".into();
                    false
                }
            },
            Err(e) => {
                self.last_error = format!("登录失败：{e}");
                false
            }
        }
    }

    /// 未完成任务；失败返回 Err（调用方保留旧数据显示离线）。每页最多 50 条，翻页到空。
    pub fn tasks(&mut self) -> Result<Vec<Value>, String> {
        let mut out = Vec::new();
        let mut page = 1i64;
        loop {
            let batch = self.authed_call(
                "GET",
                &format!("/api/v1/projects/{}/tasks?page={}", self.project, page),
                None,
            )?;
            let arr = batch.as_array().cloned().unwrap_or_default();
            let n = arr.len();
            out.extend(arr);
            if n < 50 {
                break;
            }
            page += 1;
        }
        self.last_error.clear();
        out.retain(|t| !t.get("done").and_then(|d| d.as_bool()).unwrap_or(false));
        Ok(out)
    }

    /// 添加任务：PUT /api/v1/projects/{id}/tasks
    pub fn add(
        &mut self,
        title: &str,
        due: Option<DateTime<FixedOffset>>,
        priority: i64,
    ) -> Result<(), String> {
        let mut body = json!({"title": title, "priority": priority});
        if let Some(d) = due {
            body["due_date"] = json!(d.to_rfc3339());
        }
        self.authed_call("PUT", &format!("/api/v1/projects/{}/tasks", self.project), Some(&body))
            .map(|_| ())
            .map_err(|e| {
                self.last_error = format!("添加失败：{e}");
                self.last_error.clone()
            })
    }

    /// 勾选完成：POST /api/v1/tasks/{id}，body 必须全量（漏字段会被清空）。
    pub fn set_done(&mut self, task: &Value) -> Result<(), String> {
        let id = task.get("id").and_then(|v| v.as_i64()).unwrap_or(0);
        let body = json!({
            "title": task.get("title").cloned().unwrap_or(json!("")),
            "description": task.get("description").cloned().unwrap_or(json!("")),
            "due_date": task.get("due_date").cloned().unwrap_or(Value::Null),
            "priority": task.get("priority").cloned().unwrap_or(json!(0)),
            "project_id": self.project,
            "done": true,
        });
        self.authed_call("POST", &format!("/api/v1/tasks/{id}"), Some(&body))
            .map(|_| ())
            .map_err(|e| {
                self.last_error = format!("勾选失败：{e}");
                self.last_error.clone()
            })
    }
}

/// 展示用任务信息
#[derive(Clone)]
pub struct TaskInfo {
    pub id: i64,
    pub title: String,
    pub due: Option<DateTime<FixedOffset>>,
    pub priority: i64,
    pub raw: Value,
}

impl TaskInfo {
    pub fn from_raw(v: &Value) -> Option<Self> {
        let id = v.get("id").and_then(|x| x.as_i64())?;
        Some(Self {
            id,
            title: v.get("title").and_then(|x| x.as_str()).unwrap_or("").to_string(),
            due: v.get("due_date").and_then(|x| x.as_str()).and_then(crate::parse::parse_due),
            priority: v.get("priority").and_then(|x| x.as_i64()).unwrap_or(0),
            raw: v.clone(),
        })
    }
}
