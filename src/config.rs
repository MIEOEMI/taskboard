//! 配置读取：环境变量 VIKUNJA_* → ~/.config/vikunja/env → exe 同目录 .env → %APPDATA%/vikunja/env
use std::collections::BTreeMap;
use std::path::PathBuf;

pub const KEYS: [&str; 4] = ["VIKUNJA_URL", "VIKUNJA_USER", "VIKUNJA_PASS", "VIKUNJA_PROJECT"];

fn parse_env_file(path: &std::path::Path) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    if let Ok(s) = std::fs::read_to_string(path) {
        for line in s.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') || !line.contains('=') {
                continue;
            }
            let (k, v) = line.split_once('=').unwrap();
            out.insert(k.trim().to_string(), v.trim().to_string());
        }
    }
    out
}

pub fn load() -> BTreeMap<String, String> {
    let mut cfg: BTreeMap<String, String> = BTreeMap::new();
    for k in KEYS {
        let default = if k == "VIKUNJA_PROJECT" { "2" } else { "" };
        cfg.insert(k.to_string(), std::env::var(k).unwrap_or_else(|_| default.to_string()));
    }
    if cfg.get("VIKUNJA_URL").map_or(true, |s| s.is_empty()) {
        let mut candidates: Vec<PathBuf> = Vec::new();
        if let Some(home) = std::env::var_os("HOME") {
            candidates.push(PathBuf::from(home).join(".config/vikunja/env"));
        }
        if let Ok(exe) = std::env::current_exe() {
            if let Some(dir) = exe.parent() {
                candidates.push(dir.join(".env"));
            }
        }
        if let Some(appdata) = std::env::var_os("APPDATA") {
            candidates.push(PathBuf::from(appdata).join("vikunja/env"));
        }
        for path in candidates {
            if path.exists() {
                let map = parse_env_file(&path);
                if !map.is_empty() {
                    for (k, v) in map {
                        cfg.insert(k, v); // 与 Python 版一致：文件值覆盖
                    }
                }
                if !cfg.get("VIKUNJA_URL").map_or(true, |s| s.is_empty()) {
                    break;
                }
            }
        }
    }
    cfg
}
