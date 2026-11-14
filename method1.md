# 桌面任务看板 · 教学实施方案（method1 · v2 桌面挂件版）

> 面向对象：编程入门阶段的同学（Windows 为主）
> 技术栈：Python 3.10+ / **PySide6 (Qt6)** / Vikunja REST API
> 教学模式：结对编程 —— 同学动手写代码，我出骨架、讲知识点、review、救火
> 总原则：每个阶段结束都有一个「能跑」的产物；不提前引入用不到的概念

## ⚠️ v1 → v2 为什么换框架

v1 用 NiceGUI `native=True`，那只是把网页包进普通窗口：会进任务栏、会抢焦点、
永远盖在别的软件上 —— **不是桌面挂件**。v2 改用 PySide6 原生 Qt 窗口，一行 flag
就拿到我们全部要的桌面行为：

| 需求 | Qt 方案 |
|------|---------|
| 置底（永不盖住其他软件） | `Qt.WindowStaysOnBottomHint` |
| 贴在桌面上（无标题栏、圆角、半透明） | `Qt.FramelessWindowHint` + `WA_TranslucentBackground` |
| 不出现在任务栏 / Alt-Tab | `Qt.Tool` |
| 不抢焦点 | `WA_ShowWithoutActivating` |
| 点空白处穿透（锁定模式） | ctypes 加 `WS_EX_TRANSPARENT`（Stage4） |

v1 已发的 Stage0 第 1 步（建 venv）**继续有效**；只有 hello.py 从 NiceGUI 换成
PySide6（见 Stage0）。数据层（httpx / .env / 解析器）设计不变。

---

## 一、项目最终形态

一个**贴在桌面上的半透明任务挂件**：平时沉在所有窗口底下，不挡任何软件；
想看任务时切到桌面/最小化窗口就能看到；点它上面的输入框可以一句话加任务。

```
 桌面壁纸 ▸▸▸ 挂件(半透明圆角) ▸▸▸ 其他所有软件窗口
              ┌──────────────────────────┐
              │ 我的一天        ＋ 明天 交作业 ↵ │
              │ 今天(3)   未来7天(5)   以后(8)  │
              │ ▢ 高数作业 ▢ 实验报告  ▢ 看论文 │
              │ ▢ 背单词⚠  ▢ 买火车票  ▢ 整理笔记│
              └──────────────────────────┘
   托盘图标：锁定(点击穿透) / 解锁、透明度、退出
```

- 数据来自 Vikunja，10 秒自动刷新；勾选、添加双向同步
- 位置记忆、开机自启、暗色半透明

## 二、阶段划分（验收驱动）

| 阶段 | 目标 | 产物 | 预计投入 |
|------|------|------|---------|
| Stage0 | 环境 + 第一个 Qt 窗口 | hello.py 能跑 | 10~15 分钟 |
| Stage1 | 挂件雏形：置底+无边框+半透明+三列假数据 | 真正的桌面挂件 | 1 次课 |
| Stage2 | 接 Vikunja 真数据 | 挂件显示真实任务 | 1 次课 |
| Stage3 | 一句话快速添加 | 输入即建任务 | 1 次课 |
| Stage4 | 打磨：托盘/锁定穿透/开机自启/位置记忆 | 日常可用 | 看兴趣 |

### Stage 0：环境与第一个窗口

```bash
mkdir -p ~/taskboard && cd ~/taskboard
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install PySide6
```

hello.py（替换掉 v1 的 NiceGUI 版）：

```python
from PySide6.QtWidgets import QApplication, QLabel
import sys

app = QApplication(sys.argv)
label = QLabel('你好，任务挂件！')
label.resize(280, 80)
label.show()
sys.exit(app.exec())
```

- 知识点：venv、`QApplication` 事件循环、`show()` 与 `exec()` 的关系
- 验收：弹出一个小窗口，显示问候语；Ctrl+C 关不掉 → 点 X 关（Qt 程序的标准退出方式）
- 坑：`app.exec()` 不写窗口一闪就没了；Windows 装 PySide6 若缺 VC++ 运行库，装 VS2022 Redistributable

### Stage 1：挂件雏形（本项目的灵魂一课）

- 步骤：
  1. `Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnBottomHint` 三件套
  2. `WA_TranslucentBackground` + 顶层圆角容器，做半透明暗色卡片
  3. 三列：QVBoxLayout × QHBox 嵌套，假数据 `TASKS = [{title, due, done, priority}]`
  4. `render_task(t)`：字典 → 一行卡片（QCheckBox + QLabel）
  5. 按 due 分桶「今天 / 未来7天 / 以后」
  6. 鼠标拖动：mousePressEvent/mouseMoveEvent 记 offset 移窗口
- 知识点：布局嵌套、窗口 flags（`|` 组合）、事件函数、datetime 分桶
- 验收：**打开任意全屏软件，挂件永远沉底不挡视线**；切回桌面能看见它；拖它可移动；改 TASKS 重启界面变化
- 坑：flags 必须在 show() 前设置；`Qt.Tool` 窗口默认不抢焦点是优点，但输入框仍可正常打字

### Stage 2：接 Vikunja 真数据

- `pip install httpx python-dotenv`；GET `/api/v1/tasks/all`
- `fetch_tasks()` + try/except：断网显示「离线，10 秒后重试」，不许崩
- `QTimer(10000)` 定时刷新 + 网页端勾选 → 挂件 10 秒内同步
- Token 放 `.env`，绝不进 git
- 坑：due_date 是 UTC ISO 字符串，转本地；接口有分页，任务多要翻页拉全

### Stage 3：一句话快速添加

- 顶部 QLineEdit + returnPressed 信号
- `parse(text)`：今天/明天/周X/`!高`，解析不出默认「以后」列，不报错
- POST `/api/v1/projects/{id}/tasks`，成功后立即刷新
- 知识点：信号与槽、字符串处理、正则入门

### Stage 4：打磨

- QSystemTrayIcon 托盘：锁定/解锁、透明度、退出
- 锁定模式 = ctypes `WS_EX_TRANSPARENT|WS_EX_LAYERED`：整块点击穿透，纯展示
- QSettings 位置/透明度记忆；开机自启（shell:startup 放快捷方式）

## 三、教学节奏与分工

| 谁 | 做什么 |
|----|--------|
| 我（coder） | 出骨架（留空函数）、讲知识点、review、救火 |
| 同学 | 自己敲、自己跑；报错**原样**发回（不删减） |
| 节奏 | 一小步一跑通；Stage0+1 可合并为第一次课 |

纪律：每行代码过 review 但不代写整段；验收清单全绿才进下一阶段；卡 20 分钟直接发我。

## 四、风险与备选

| 风险 | 应对 |
|------|------|
| `WindowStaysOnBottomHint` 在个别桌面环境不生效 | Windows 主目标完全支持；失效则退「普通窗口+Tool」并说明差异 |
| PySide6 安装体积大 (~150MB) | 一次性成本；教学机提前装好 |
| 缺 VC++ 运行库 | 装 VS2022 Redistributable x64 |
| Vikunja 连不上 | Stage1 全程离线可用 |
| Token 泄漏 | `.env` + 最小权限 + 不进 git |

## 五、验收清单

- [ ] Stage0：venv 装包不报错；Qt 窗口出现问候语
- [ ] Stage1：无边框半透明三列挂件；**全屏软件盖不住它**；可拖动；不进任务栏
- [ ] Stage2：真实任务显示；网页端勾选 10 秒内同步；断网不崩
- [ ] Stage3：「明天 XX」建成带截止日的任务；解析不出不报错
- [ ] Stage4：托盘可用；锁定后鼠标穿透挂件；重启电脑自启且位置不变
