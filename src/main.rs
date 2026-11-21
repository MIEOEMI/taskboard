//! 桌面任务挂件 · Rust + Slint 版
//!
//! 用法：
//!   taskboard               正常启动（托盘 + 置底 + 10 秒同步）
//!   taskboard --no-tray     不创建托盘（无系统托盘的环境）
//!   taskboard --no-anim     关闭动效（光斑定格、不做呼吸/入场动画）
//!   taskboard --selftest    无头自检：拉任务渲染一帧，打印任务数与窗口尺寸后退出
//!
//! 凭据优先级：环境变量 VIKUNJA_* → ~/.config/vikunja/env → exe 同目录 .env
mod api;
mod config;
mod parse;

use api::{TaskInfo, Vikunja};
use chrono::{DateTime, FixedOffset};
use serde_json::Value;
use slint::{
    ComponentHandle, LogicalPosition, Model, ModelRc, PhysicalPosition, SharedString, VecModel,
    WindowPosition,
};
use std::cell::{Cell, RefCell};
use std::collections::HashSet;
use std::rc::Rc;
use std::sync::mpsc;
use std::time::Duration;

slint::include_modules!();

const REFRESH_SECS: u64 = 10;

enum WorkerCmd {
    Refresh,
    Done(Value),
    Add(String, Option<DateTime<FixedOffset>>, i64),
}

enum UiMsg {
    Tasks(Result<Vec<Value>, String>),
    DoneOk(Result<(), String>),
    Added(Result<String, String>),
}

#[derive(Clone, Copy, Debug)]
enum TrayCmd {
    Center,
    Preview,
    Quit,
}

#[derive(Clone)]
struct Models {
    today: Rc<VecModel<TaskData>>,
    soon: Rc<VecModel<TaskData>>,
    later: Rc<VecModel<TaskData>>,
}

#[derive(Default)]
struct Shared {
    tasks: Vec<Value>,
    sig: Vec<(i64, String, String, i64)>,
    seen: HashSet<i64>,
    first: bool,
    last_err: String,
}

fn sig_of(tasks: &[Value]) -> Vec<(i64, String, String, i64)> {
    let mut sig: Vec<_> = tasks
        .iter()
        .map(|t| {
            (
                t.get("id").and_then(|v| v.as_i64()).unwrap_or(0),
                t.get("due_date").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                t.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                t.get("priority").and_then(|v| v.as_i64()).unwrap_or(0),
            )
        })
        .collect();
    sig.sort_by_key(|x| x.0);
    sig
}

fn accent_color(info: &TaskInfo, overdue: bool) -> slint::Color {
    if overdue {
        slint::Color::from_rgb_u8(255, 107, 107) // RED
    } else if info.priority >= 3 {
        slint::Color::from_rgb_u8(242, 181, 68) // AMBER
    } else {
        slint::Color::from_rgb_u8(77, 159, 255) // BLUE
    }
}

/// 按三列重建模型；respect_new=true 时只有新任务播错峰入场动画。
fn rebuild_models(app: &AppWindow, models: &Models, shared: &mut Shared, respect_new: bool) {
    let now = parse::now_shanghai();
    let mut buckets: [Vec<TaskData>; 3] = [Vec::new(), Vec::new(), Vec::new()];
    let mut infos: Vec<TaskInfo> = shared.tasks.iter().filter_map(TaskInfo::from_raw).collect();
    infos.sort_by_key(|i| {
        i.raw
            .get("due_date")
            .and_then(|v| v.as_str())
            .unwrap_or("9999")
            .to_string()
    });
    for info in &infos {
        let (label, overdue) = parse::due_label(info.due, now);
        let b = match parse::bucket(info.due, now) {
            "today" => 0,
            "soon" => 1,
            _ => 2,
        };
        let is_new = !respect_new || (shared.first || !shared.seen.contains(&info.id));
        buckets[b].push(TaskData {
            id: info.id as i32,
            title: SharedString::from(info.title.clone()),
            time: SharedString::from(label),
            accent: accent_color(info, overdue),
            overdue,
            high: info.priority >= 3,
            is_new,
            dying: false,
        });
    }
    let s = app.global::<Store>();
    models.today.set_vec(buckets[0].clone());
    models.soon.set_vec(buckets[1].clone());
    models.later.set_vec(buckets[2].clone());
    s.set_n_today(buckets[0].len() as i32);
    s.set_n_soon(buckets[1].len() as i32);
    s.set_n_later(buckets[2].len() as i32);
    s.set_n_total(shared.tasks.len() as i32);
    shared.seen = infos.iter().map(|i| i.id).collect();
    shared.sig = sig_of(&shared.tasks);
}

fn set_row_dying(models: &Models, id: i64) {
    let id32 = id as i32;
    for m in [&models.today, &models.soon, &models.later] {
        for i in 0..m.row_count() {
            if let Some(mut d) = m.row_data(i) {
                if d.id == id32 {
                    d.dying = true;
                    m.set_row_data(i, d);
                    return;
                }
            }
        }
    }
}

fn remove_row(app: &AppWindow, models: &Models, shared: &Rc<RefCell<Shared>>, id: i64) {
    let id32 = id as i32;
    for m in [&models.today, &models.soon, &models.later] {
        for i in 0..m.row_count() {
            if m.row_data(i).map(|d| d.id).unwrap_or(-1) == id32 {
                m.remove(i);
                break;
            }
        }
    }
    let mut sh = shared.borrow_mut();
    sh.tasks.retain(|t| t.get("id").and_then(|v| v.as_i64()) != Some(id));
    sh.sig = sig_of(&sh.tasks);
    rebuild_models(app, models, &mut sh, false);
}

fn pos_file() -> std::path::PathBuf {
    let mut base = std::env::var_os("HOME")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| std::path::PathBuf::from("."));
    base.push(".config/taskboard/pos.txt");
    base
}

fn center_on_screen(app: &AppWindow) {
    let (sw, sh) = screen_size();
    let size = app.window().size();
    let x = ((sw - size.width as f32) / 2.0).max(0.0) as i32;
    let y = ((sh - size.height as f32) / 2.0).max(0.0) as i32;
    app.window().set_position(WindowPosition::Physical(PhysicalPosition::new(x, y)));
}

fn preview_on_top(app: &AppWindow, flag: Rc<Cell<bool>>) {
    set_topmost(app, true);
    center_on_screen(app);
    let st = app.global::<Store>();
    st.set_status_text("置顶预览 6s".into());
    flag.set(true);
    let weak = app.as_weak();
    let flag2 = flag.clone();
    slint::Timer::single_shot(Duration::from_secs(6), move || {
        if let Some(app) = weak.upgrade() {
            set_topmost(&app, false);
            app.global::<Store>().set_status_text("回到桌面模式".into());
        }
        flag2.set(false);
    });
}

#[cfg(target_os = "linux")]
fn screen_size() -> (f32, f32) {
    use x11rb::connection::Connection;
    if let Ok((conn, screen_num)) = x11rb::connect(None) {
        let screen = &conn.setup().roots[screen_num];
        return (screen.width_in_pixels as f32, screen.height_in_pixels as f32);
    }
    (1280.0, 800.0)
}

#[cfg(target_os = "windows")]
fn screen_size() -> (f32, f32) {
    unsafe {
        use windows_sys::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSCREEN, SM_CYSCREEN};
        (
            GetSystemMetrics(SM_CXSCREEN) as f32,
            GetSystemMetrics(SM_CYSCREEN) as f32,
        )
    }
}

#[cfg(target_os = "windows")]
fn start_bottom_keepalive(weak: slint::Weak<AppWindow>, preview: Rc<Cell<bool>>) {
    let timer = slint::Timer::default();
    let timer = Box::leak(Box::new(timer));
    timer.start(slint::TimerMode::Repeated, Duration::from_secs(3), move || {
        if preview.get() {
            return; // 置顶预览期间不压底
        }
        if let Some(app) = weak.upgrade() {
            if let Some(hwnd) = hwnd_of(&app) {
                push_bottom(hwnd);
            }
        }
    });
}

#[cfg(target_os = "windows")]
fn hwnd_of(_app: &AppWindow) -> Option<isize> {
    // Slint 的 Window 不实现 HasWindowHandle，改为按标题查找自己的窗口。
    // 标题在 app.slint 里固定为 WINDOW_TITLE。
    use windows_sys::Win32::UI::WindowsAndMessaging::FindWindowW;
    let title: Vec<u16> = "TaskBoardWidget\0".encode_utf16().collect();
    unsafe {
        let h = FindWindowW(std::ptr::null(), title.as_ptr());
        if h.is_null() { None } else { Some(h as isize) }
    }
}

#[cfg(target_os = "windows")]
fn push_bottom(hwnd: isize) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        SetWindowPos, HWND_BOTTOM, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE,
    };
    unsafe {
        SetWindowPos(
            hwnd as _,
            HWND_BOTTOM,
            0,
            0,
            0,
            0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
        );
    }
}

#[cfg(target_os = "windows")]
fn push_top(hwnd: isize, on: bool) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        SetWindowPos, HWND_NOTOPMOST, HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE,
    };
    let insert_after = if on { HWND_TOPMOST } else { HWND_NOTOPMOST };
    unsafe {
        SetWindowPos(
            hwnd as _,
            insert_after,
            0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
        );
    }
}

/// 置顶/取消置顶（Windows 走 SetWindowPos；其他平台是空操作，仅居中提示）
fn set_topmost(app: &AppWindow, on: bool) {
    #[cfg(target_os = "windows")]
    {
        if let Some(hwnd) = hwnd_of(app) {
            push_top(hwnd, on);
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = (app, on);
    }
}

// ---- 托盘 ----
fn tray_icon_rgba() -> (Vec<u8>, u32, u32) {
    // 32x32：绿色圆角方块 + 白色横杠（与 Python 版 make_icon 观感一致）
    const S: u32 = 32;
    let mut px = vec![0u8; (S * S * 4) as usize];
    for y in 0..S {
        for x in 0..S {
            let fx = x as f32 - 15.5;
            let fy = y as f32 - 15.5;
            let half = 13.0f32;
            let in_box = fx.abs() <= half && fy.abs() <= half;
            let dx = fx.abs() - (half - 8.0);
            let dy = fy.abs() - (half - 8.0);
            let inside = if dx > 0.0 && dy > 0.0 { dx * dx + dy * dy <= 64.0 } else { in_box };
            let idx = ((y * S + x) * 4) as usize;
            if inside {
                px[idx] = 76;
                px[idx + 1] = 159;
                px[idx + 2] = 112;
                px[idx + 3] = 255;
            }
            if inside && x >= 9 && x <= 23 && y >= 15 && y <= 17 {
                px[idx] = 255;
                px[idx + 1] = 255;
                px[idx + 2] = 255;
                px[idx + 3] = 255;
            }
        }
    }
    (px, S, S)
}

fn spawn_tray() {
    std::thread::spawn(move || {
        if let Err(e) = run_tray() {
            eprintln!("托盘不可用：{e}（可用 --no-tray 关闭）");
        }
    });
}

fn run_tray() -> Result<(), String> {
    use tray_icon::menu::{Menu, MenuItem};
    use tray_icon::TrayIconBuilder;

    let (rgba, w, h) = tray_icon_rgba();
    let icon = tray_icon::Icon::from_rgba(rgba, w, h).map_err(|e| e.to_string())?;

    let menu = Menu::new();
    let mi_center = MenuItem::with_id("center", "移到屏幕中央", true, None::<muda::accelerator::Accelerator>);
    let mi_preview = MenuItem::with_id("preview", "置顶预览 6 秒", true, None::<muda::accelerator::Accelerator>);
    let mi_quit = MenuItem::with_id("quit", "退出", true, None::<muda::accelerator::Accelerator>);
    menu.append(&mi_center).map_err(|e| e.to_string())?;
    menu.append(&mi_preview).map_err(|e| e.to_string())?;
    menu.append(&mi_quit).map_err(|e| e.to_string())?;

    let _tray = TrayIconBuilder::new()
        .with_icon(icon)
        .with_tooltip("Hermes 任务挂件")
        .with_menu(Box::new(menu))
        .build()
        .map_err(|e| e.to_string())?;

    #[cfg(target_os = "linux")]
    {
        gtk::init().map_err(|e| e.to_string())?;
        gtk::main(); // 阻塞本线程；菜单事件走 muda 全局 channel，由 UI 线程轮询
    }
    #[cfg(target_os = "windows")]
    {
        use windows_sys::Win32::UI::WindowsAndMessaging::{
            DispatchMessageW, GetMessageW, TranslateMessage, MSG,
        };
        unsafe {
            let mut msg: MSG = std::mem::zeroed();
            while GetMessageW(&mut msg, std::ptr::null_mut(), 0, 0) > 0 {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
    }
    #[cfg(target_os = "macos")]
    {
        loop {
            std::thread::park();
        }
    }
    Ok(())
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let selftest = args.iter().any(|a| a == "--selftest");
    let no_tray = selftest || args.iter().any(|a| a == "--no-tray");
    let no_anim = args.iter().any(|a| a == "--no-anim");

    let cfg = config::load();
    let app = AppWindow::new().expect("Slint 初始化失败");
    let app = Rc::new(app);

    let models = Models {
        today: Rc::new(VecModel::default()),
        soon: Rc::new(VecModel::default()),
        later: Rc::new(VecModel::default()),
    };
    {
        let s = app.global::<Store>();
        s.set_today(ModelRc::from(models.today.clone()));
        s.set_soon(ModelRc::from(models.soon.clone()));
        s.set_later(ModelRc::from(models.later.clone()));
    }

    let shared = Rc::new(RefCell::new(Shared::default()));
    let preview_flag = Rc::new(Cell::new(false));

    // ---- 位置记忆 ----
    {
        let mut p = (90.0f32, 90.0f32);
        if let Ok(txt) = std::fs::read_to_string(pos_file()) {
            if let Some((x, y)) = txt.trim().split_once(',') {
                if let (Ok(a), Ok(b)) = (x.trim().parse(), y.trim().parse()) {
                    p = (a, b);
                }
            }
        }
        app.window().set_position(WindowPosition::Logical(LogicalPosition::new(p.0, p.1)));
        let pos = Rc::new(RefCell::new(p));
        let s = app.global::<Store>();
        {
            let weak = app.as_weak();
            let pos = pos.clone();
            s.on_drag_by(move |dx, dy| {
                if let Some(app) = weak.upgrade() {
                    let mut p = pos.borrow_mut();
                    p.0 += dx;
                    p.1 += dy;
                    app.window()
                        .set_position(WindowPosition::Logical(LogicalPosition::new(p.0, p.1)));
                }
            });
        }
        {
            let pos = pos.clone();
            s.on_save_pos(move || {
                let p = pos.borrow();
                if let Some(dir) = pos_file().parent() {
                    let _ = std::fs::create_dir_all(dir);
                }
                let _ = std::fs::write(pos_file(), format!("{},{}", p.0, p.1));
            });
        }
    }

    // ---- worker 线程：所有 HTTP 都在这边，UI 不卡 ----
    let (tx_worker, rx_worker) = mpsc::channel::<WorkerCmd>();
    let (tx_ui, rx_ui) = mpsc::channel::<UiMsg>();
    std::thread::spawn(move || {
        let mut vk = Vikunja::new(&cfg);
        while let Ok(cmd) = rx_worker.recv() {
            match cmd {
                WorkerCmd::Refresh => {
                    let _ = tx_ui.send(UiMsg::Tasks(vk.tasks()));
                }
                WorkerCmd::Done(t) => {
                    let _ = tx_ui.send(UiMsg::DoneOk(vk.set_done(&t)));
                }
                WorkerCmd::Add(title, due, prio) => {
                    let _ = tx_ui.send(UiMsg::Added(vk.add(&title, due, prio).map(|_| title)));
                }
            }
        }
    });

    // ---- 交互回调 ----
    {
        let s = app.global::<Store>();
        let weak = app.as_weak();
        let shared = shared.clone();
        let tx = tx_worker.clone();
        let models2 = models.clone();
        s.on_done(move |id| {
            let id = id as i64;
            let Some(app) = weak.upgrade() else { return };
            let raw = shared
                .borrow()
                .tasks
                .iter()
                .find(|t| t.get("id").and_then(|v| v.as_i64()) == Some(id))
                .cloned();
            if let Some(t) = raw {
                let _ = tx.send(WorkerCmd::Done(t));
            }
            set_row_dying(&models2, id); // Slint 侧播放填色 + 打勾 + 收起动画
            let weak2 = weak.clone();
            let shared2 = shared.clone();
            let models3 = models2.clone();
            slint::Timer::single_shot(Duration::from_millis(450), move || {
                if let Some(app) = weak2.upgrade() {
                    remove_row(&app, &models3, &shared2, id);
                }
            });
            let _ = app;
        });
    }
    {
        let tx = tx_worker.clone();
        let s = app.global::<Store>();
        s.on_add_task(move |raw| {
            let now = parse::now_shanghai();
            let (title, due, prio) = parse::parse(raw.trim(), now);
            let _ = tx.send(WorkerCmd::Add(title, due, prio as i64));
        });
    }

    // ---- 托盘命令通道 ----
    let (tx_tray, rx_tray) = mpsc::channel::<TrayCmd>();

    // ---- 消息轮询（100ms）：托盘命令 / 菜单事件 / worker 回包 ----
    {
        let weak = app.as_weak();
        let shared = shared.clone();
        let models2 = models.clone();
        let tx_worker = tx_worker.clone();
        let tx_tray = tx_tray.clone();
        let preview_flag_loop = preview_flag.clone();
        let selftest_fired = Rc::new(Cell::new(false));
        let timer = Box::leak(Box::new(slint::Timer::default()));
        timer.start(slint::TimerMode::Repeated, Duration::from_millis(100), move || {
            let Some(app) = weak.upgrade() else { return };

            while let Ok(c) = rx_tray.try_recv() {
                match c {
                    TrayCmd::Center => {
                        center_on_screen(&app);
                    }
                    TrayCmd::Preview => preview_on_top(&app, preview_flag_loop.clone()),
                    TrayCmd::Quit => std::process::exit(0),
                }
            }
            use tray_icon::menu::MenuEvent;
            while let Ok(ev) = MenuEvent::receiver().try_recv() {
                let cmd = match ev.id().as_ref() {
                    "center" => Some(TrayCmd::Center),
                    "preview" => Some(TrayCmd::Preview),
                    "quit" => Some(TrayCmd::Quit),
                    _ => None,
                };
                if let Some(c) = cmd {
                    let _ = tx_tray.send(c);
                }
            }

            let mut got_tasks = false;
            while let Ok(msg) = rx_ui.try_recv() {
                let st = app.global::<Store>();
                match msg {
                    UiMsg::Tasks(r) => {
                        st.set_busy(false);
                        match r {
                            Ok(tasks) => {
                                let mut sh = shared.borrow_mut();
                                let new_sig = sig_of(&tasks);
                                let changed = new_sig != sh.sig || sh.first;
                                sh.last_err.clear();
                                sh.tasks = tasks;
                                if changed {
                                    rebuild_models(&app, &models2, &mut sh, true);
                                }
                                drop(sh);
                                st.set_status_color(slint::Color::from_rgb_u8(76, 159, 112));
                                st.set_status_text(SharedString::from(format!(
                                    "已同步 {}",
                                    parse::now_shanghai().format("%H:%M:%S")
                                )));
                            }
                            Err(e) => {
                                shared.borrow_mut().last_err = e.clone();
                                st.set_status_color(slint::Color::from_rgb_u8(255, 107, 107));
                                st.set_status_text(SharedString::from(format!("{e}，10 秒后重试")));
                            }
                        }
                        got_tasks = true;
                    }
                    UiMsg::DoneOk(r) => match r {
                        Ok(()) => {
                            st.set_status_color(slint::Color::from_rgb_u8(76, 159, 112));
                            st.set_status_text("已完成 1 项".into());
                        }
                        Err(e) => {
                            st.set_status_color(slint::Color::from_rgb_u8(255, 107, 107));
                            st.set_status_text(SharedString::from(e));
                        }
                    },
                    UiMsg::Added(r) => match r {
                        Ok(title) => {
                            st.set_status_color(slint::Color::from_rgb_u8(76, 159, 112));
                            st.set_status_text(SharedString::from(format!("已添加：{title}")));
                            let _ = tx_worker.send(WorkerCmd::Refresh);
                        }
                        Err(e) => {
                            st.set_status_color(slint::Color::from_rgb_u8(255, 107, 107));
                            st.set_status_text(SharedString::from(e));
                        }
                    },
                }
            }

            if selftest && got_tasks && !selftest_fired.get() {
                selftest_fired.set(true);
                let weak2 = weak.clone();
                let shared2 = shared.clone();
                slint::Timer::single_shot(Duration::from_millis(1200), move || {
                    let sh = shared2.borrow();
                    println!(
                        "tasks loaded: {} | api error: {}",
                        sh.tasks.len(),
                        if sh.last_err.is_empty() { "-" } else { &sh.last_err }
                    );
                    if let Some(app) = weak2.upgrade() {
                        let sz = app.window().size();
                        println!("window size: {}x{} (physical)", sz.width, sz.height);
                    }
                    println!("frameless: true | translucent: true");
                    // 截图：把一帧原始 RGBA 落盘，交给外部转 PNG（不引入 image 依赖）
                    if let Some(app) = weak2.upgrade() {
                        if let Ok(buf) = app.window().take_snapshot() {
                            let w = buf.width();
                            let h = buf.height();
                            let raw: Vec<u8> = buf.as_slice().iter().flat_map(|p| [p.r, p.g, p.b, p.a]).collect();
                            let path = std::env::var("SHOT_PATH")
                                .unwrap_or_else(|_| "shot.raw".to_string());
                            let _ = std::fs::write(&path, &raw);
                            println!("shot: {path} {w}x{h} {} bytes", raw.len());
                        } else {
                            println!("shot: take_snapshot 不支持当前后端");
                        }
                    }
                    for sample in ["明天 9点 交作业 !高", "周五 17:00 交实验报告", "周会 无日期"] {
                        let now = parse::now_shanghai();
                        let (t, d, p) = parse::parse(sample, now);
                        println!(
                            "parse {sample:?} -> title={t:?} due={} prio={p}",
                            d.map(|d| d.to_rfc3339()).unwrap_or_else(|| "None".into())
                        );
                    }
                    let _ = slint::quit_event_loop();
                });
            }
        });
    }

    // ---- 时钟：驱动光斑漂移 / 呼吸 / 入场动画 ----
    if !no_anim {
        let weak = app.as_weak();
        let clock_timer = Box::leak(Box::new(slint::Timer::default()));
        let mut tick = 0u64;
        clock_timer.start(slint::TimerMode::Repeated, Duration::from_millis(50), move || {
            tick += 1;
            if let Some(app) = weak.upgrade() {
                let t = tick as f64 * 0.05;
                let st = app.global::<Store>();
                st.set_clock(t as f32);
                st.set_phase(((t % 18.0) / 18.0) as f32);
            }
        });
    }

    #[cfg(target_os = "windows")]
fn push_top(hwnd: isize, on: bool) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        SetWindowPos, HWND_NOTOPMOST, HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE,
    };
    let insert_after = if on { HWND_TOPMOST } else { HWND_NOTOPMOST };
    unsafe {
        SetWindowPos(
            hwnd as _,
            insert_after,
            0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
        );
    }
}

/// 置顶/取消置顶（Windows 走 SetWindowPos；其他平台是空操作，仅居中提示）
fn set_topmost(app: &AppWindow, on: bool) {
    #[cfg(target_os = "windows")]
    {
        if let Some(hwnd) = hwnd_of(app) {
            push_top(hwnd, on);
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = (app, on);
    }
}

// ---- 托盘 ----
    if !no_tray {
        spawn_tray();
    }

    // ---- 10 秒定时刷新 ----
    {
        let tx = tx_worker.clone();
        let st = app.global::<Store>();
        st.set_busy(true);
        st.set_status_color(slint::Color::from_rgb_u8(77, 159, 255));
        st.set_status_text("同步中…".into());
        let _ = tx.send(WorkerCmd::Refresh);
        let refresh_timer = Box::leak(Box::new(slint::Timer::default()));
        refresh_timer.start(
            slint::TimerMode::Repeated,
            Duration::from_secs(REFRESH_SECS),
            move || {
                let _ = tx.send(WorkerCmd::Refresh);
            },
        );
    }

    app.show().expect("窗口显示失败");

    // ---- Windows：每 3 秒 SetWindowPos(HWND_BOTTOM) 压底 ----
    #[cfg(target_os = "windows")]
    start_bottom_keepalive(app.as_weak(), preview_flag.clone());

    app.run().expect("事件循环异常退出");
}