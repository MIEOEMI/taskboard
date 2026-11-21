# TaskBoard · 桌面任务挂件

贴在 Windows / Ubuntu 桌面上的 Vikunja 任务看板：置底、无边框、圆角半透明、不进任务栏、不抢焦点。
三列（今天 / 未来 7 天 / 以后），勾选即回写，一句话加任务。

Rust + Slint 实现。**实测峰值内存 68 MB**，单文件成品 Windows 11.7 MB / Linux 17.8 MB。

## 下载

https://github.com/MIEOEMI/taskboard/releases/latest

- `TaskBoard.exe` — Windows
- `TaskBoard` — Linux

## 用之前

在可执行文件**旁边**放一个 `.env`：

```
VIKUNJA_URL=https://tasks.funnycode.site
VIKUNJA_USER=你的用户名
VIKUNJA_PASS=你的密码
VIKUNJA_PROJECT=2
```

## 一句话添加

输入框里按回车即可，支持：

| 写法 | 解析结果 |
|---|---|
| `明天 9点 交作业` | 明天 09:00 |
| `周五 17:00 交实验报告` | 本周/下周最近的周五 17:00 |
| `下周一 8点 交作业` | 下周一 08:00 |
| `11月28日 09:30 实验室` | 11-28 09:30 |
| `11-25 20:30 组会` | 11-25 20:30 |
| `3天后 交材料` | 三天后 23:59 |
| `周日 19:30-21:30 开发` | 取开始时间 19:30 |
| `今天晚上8点 复习高数` | 今天 20:00 |
| `周五 交报告` | 周五 23:59 |
| `交水电费` | 无日期（进「以后」列） |

后缀 `!高` / `!低` 设优先级。

## 找回挂件

托盘图标右键 →「移到屏幕中央」/「置顶预览 6 秒」。

## 开发

```bash
cargo test                 # 解析器 11 条用例
cargo build --release
cargo run -- --selftest    # 无头自检：拉任务 + 打印解析结果
```

架构：`src/parse.rs` 解析 + 分桶，`src/api.rs` Vikunja HTTP，`src/config.rs` 配置查找，
`src/main.rs` 组装与事件循环，`ui/*.slint` 界面。

## 几个坑（给以后的自己）

1. **Slint 布局不给 `horizontal-stretch` / `vertical-stretch` 会塌成 0**：截图里只剩顶栏，
   进度正常、无报错，纯视觉静默失败。
2. **正则别写多行 raw string**：缩进和换行会变成模式的一部分，几乎匹配不上任何输入。
3. **渲染器决定内存**：默认 femtovg/GL 建上下文要 ~120 MB；`renderer-software` 后
   整体 68 MB，光效/透明度/阴影都还在。
4. **Slint 的 Window 不实现 `HasWindowHandle`**：Windows 上要拿 HWND 只能按标题
   `FindWindowW`（窗口标题固定为 `TaskBoardWidget`）。

## 历史

Python/PySide6 版在 `legacy/`，功能等价但内存 ~93 MB。
