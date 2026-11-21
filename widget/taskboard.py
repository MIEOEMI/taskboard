#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面任务挂件 · 动效版（Windows / Ubuntu 通用）。

贴在壁纸上的半透明看板：置底、不进任务栏、不抢焦点、可拖动、可锁定穿透。
数据直连自建 Vikunja（今天 / 未来 7 天 / 以后 三列），10 秒自动同步。

外观层：
  · 玻璃渐变卡片 + 投影 + 顶部高光
  · 任务行错峰淡入（fade + 展开）
  · 悬停柔和高亮（自绘动画）
  · 勾选方块：填色 + 打勾动画 → 整行收起后消失
  · 逾期红点呼吸；同步中状态点呼吸
  · 列头计数徽章、优先级色条

    python taskboard.py              # 正常启动
    python taskboard.py --selftest   # 无界面自检（截图 + flags + 解析用例）
    python taskboard.py --no-anim    # 关掉动效（低配机 / 录屏）

凭据优先级：环境变量 → ~/.config/vikunja/env → 脚本同目录 .env
    VIKUNJA_URL / VIKUNJA_USER / VIKUNJA_PASS / VIKUNJA_PROJECT
"""
from __future__ import annotations

import html
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PySide6.QtCore import (
    Property, QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF,
    QSettings, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QScrollArea, QSizePolicy,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)

TZ = ZoneInfo("Asia/Shanghai")
REFRESH_MS = 10_000
COLUMNS = (("today", "今天"), ("soon", "未来 7 天"), ("later", "以后"))

ACCENT = QColor("#4c9f70")
BLUE = QColor("#4d9fff")
AMBER = QColor("#f2b544")
RED = QColor("#ff6b6b")
TXT = "#eef2f8"
DIM = "#8b93a3"

ANIM = True          # 全局动效开关（selftest / --no-anim 时关掉）


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def load_config() -> dict[str, str]:
    cfg = {
        "VIKUNJA_URL": os.environ.get("VIKUNJA_URL", ""),
        "VIKUNJA_USER": os.environ.get("VIKUNJA_USER", ""),
        "VIKUNJA_PASS": os.environ.get("VIKUNJA_PASS", ""),
        "VIKUNJA_PROJECT": os.environ.get("VIKUNJA_PROJECT", "2"),
    }
    if not cfg["VIKUNJA_URL"]:
        candidates = [
            os.path.expanduser("~/.config/vikunja/env"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
            os.path.join(os.environ.get("APPDATA", ""), "vikunja", "env"),
        ]
        for path in candidates:
            if path and os.path.exists(path):
                for line in open(path, encoding="utf-8"):
                    line = line.strip()
                    if line and "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        cfg[k.strip()] = v.strip()
                if cfg["VIKUNJA_URL"]:
                    break
    return cfg


# --------------------------------------------------------------------------- #
# Vikunja 数据层（只用标准库，断网不崩）
# --------------------------------------------------------------------------- #
class Vikunja:
    def __init__(self, cfg: dict[str, str]) -> None:
        self.base = (cfg.get("VIKUNJA_URL") or "").rstrip("/")
        self.user = cfg.get("VIKUNJA_USER", "")
        self.password = cfg.get("VIKUNJA_PASS", "")
        self.project = int(cfg.get("VIKUNJA_PROJECT") or 2)
        self.token: str | None = None
        self.last_error = ""

    def _call(self, method: str, path: str, body=None, auth: bool = True):
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
        )
        req.add_header("Content-Type", "application/json")
        if auth and self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
        return json.loads(raw) if raw.strip() else {}

    def login(self) -> bool:
        if not self.base:
            self.last_error = "缺少 VIKUNJA_URL"
            return False
        try:
            self.token = self._call(
                "POST", "/api/v1/login",
                {"username": self.user, "password": self.password}, auth=False,
            )["token"]
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = f"登录失败：{exc}"
            return False

    def tasks(self) -> list[dict] | None:
        """未完成任务；失败返回 None（调用方保留旧数据并显示离线）。"""
        if not self.token and not self.login():
            return None
        try:
            out, page = [], 1
            while True:  # 接口每页最多 50 条，必须翻页
                batch = self._call("GET", f"/api/v1/projects/{self.project}/tasks?page={page}")
                if not batch:
                    break
                out += batch
                if len(batch) < 50:
                    break
                page += 1
            self.last_error = ""
            return [t for t in out if not t.get("done")]
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):  # token 过期就重登一次
                if self.login():
                    return self.tasks()
            self.last_error = f"读取失败：{exc}"
            return None
        except Exception as exc:
            self.last_error = f"离线：{exc}"
            return None

    def add(self, title: str, due: datetime | None, priority: int = 2) -> bool:
        if not self.token and not self.login():
            return False
        body = {"title": title, "priority": priority}
        if due:
            body["due_date"] = due.isoformat()
        try:
            self._call("PUT", f"/api/v1/projects/{self.project}/tasks", body)
            return True
        except Exception as exc:
            self.last_error = f"添加失败：{exc}"
            return False

    def set_done(self, task: dict, done: bool = True) -> bool:
        """更新走 POST，且是全量替换：漏掉的字段会被清空。"""
        if not self.token and not self.login():
            return False
        body = {
            "title": task["title"],
            "description": task.get("description") or "",
            "due_date": task.get("due_date"),
            "priority": task.get("priority", 0),
            "project_id": self.project,
            "done": done,
        }
        try:
            self._call("POST", f"/api/v1/tasks/{task['id']}", body)
            return True
        except Exception as exc:
            self.last_error = f"勾选失败：{exc}"
            return False


# --------------------------------------------------------------------------- #
# 一句话解析：今天/明天/周X/!高
# --------------------------------------------------------------------------- #
WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
TIME_RE = re.compile(
    r"(?:(?P<prefix>今天|明天|后天|大后天|下周|本周|这周|周|星期)(?P<wd>[一二三四五六日天])?)?"
    r"\s*(?:(?P<mon>\d{1,2})[月/](?P<day>\d{1,2})[日号]?|(?P<mon2>\d{1,2})-(?P<day2>\d{1,2})(?![\d:：]))?"
    r"\s*(?:(?P<rel>\d+)\s*天后)?"
    r"\s*(?P<period>上午|早上|早晨|中午|下午|傍晚|晚上)?"
    r"\s*(?:(?P<hh>\d{1,2})(?:(?P<half>点半)|(?P<sep>[:：点时])(?:(?P<mm>\d{1,2})\s*分?)?)?"
    r"(?:\s*[-~—–至到]\s*\d{1,2}(?:(?:[:：点时])\d{1,2}\s*分?)?)?)?"
)
PERIOD_ADD = {"下午": 12, "傍晚": 12, "晚上": 12, "夜里": 12, "中午": 12}


def parse(text: str, now: datetime | None = None) -> tuple[str, datetime | None, int]:
    """「明天 9点 交作业 !高」→ (标题, 截止时间, 优先级)。解析不出日期就返回 None。"""
    now = now or datetime.now(TZ)
    priority = 2
    if "!高" in text:
        priority, text = 3, text.replace("!高", "")
    elif "!低" in text:
        priority, text = 1, text.replace("!低", "")
    text = text.strip()

    m = TIME_RE.search(text)
    if not m or not any(m.group(g) for g in ("prefix", "mon", "mon2", "rel", "hh")):
        return text, None, priority

    today = now.date()
    day = None
    prefix, wd = m.group("prefix"), m.group("wd")
    if prefix in ("今天", "明天", "后天", "大后天"):
        day = today + timedelta(days={"今天": 0, "明天": 1, "后天": 2, "大后天": 3}[prefix])
    elif prefix and wd:
        target = WEEKDAY[wd]
        if prefix == "下周":
            day = today + timedelta(days=(7 - today.weekday()) + target)
        else:
            day = today + timedelta(days=(target - today.weekday()) % 7)
    elif m.group("mon") or m.group("mon2"):
        mon = int(m.group("mon") or m.group("mon2"))
        dd = int((m.group("day") or m.group("day2")) or 1)
        try:
            day = today.replace(year=today.year + (1 if mon < today.month - 6 else 0),
                                month=mon, day=dd)
        except ValueError:
            day = None
    elif m.group("rel"):
        day = today + timedelta(days=int(m.group("rel")))
    if day is None:
        return text, None, priority

    if m.group("hh"):
        hour = int(m.group("hh")) + PERIOD_ADD.get(m.group("period") or "", 0)
        minute = 30 if m.group("half") else int(m.group("mm") or 0)
        hour = hour - 12 if hour >= 24 else hour
    else:
        hour, minute = 23, 59
    due = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    title = (text[: m.start()] + " " + text[m.end():]).strip(" ，,、-~—") or text
    return title, due, priority


def bucket(task: dict, now: datetime) -> str:
    due = task.get("due_date")
    if not due:
        return "later"
    d = datetime.fromisoformat(due.replace("Z", "+00:00")).astimezone(TZ)
    if d.date() <= now.date():
        return "today"
    if d.date() <= now.date() + timedelta(days=7):
        return "soon"
    return "later"


def due_label(task: dict, now: datetime) -> tuple[str, bool]:
    due = task.get("due_date")
    if not due:
        return "", False
    d = datetime.fromisoformat(due.replace("Z", "+00:00")).astimezone(TZ)
    delta = (d.date() - now.date()).days
    hm = d.strftime("%H:%M") if (d.hour, d.minute) != (23, 59) else ""
    if delta < 0:
        return f"逾期{-delta}天 {hm}".strip(), True
    if delta == 0:
        return f"{hm}".strip(), False
    return f"{d.month}/{d.day} {hm}".strip(), False


def lerp(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


# --------------------------------------------------------------------------- #
# 小部件：动效勾选框 / 呼吸点 / 计数徽章
# --------------------------------------------------------------------------- #
class AnimCheck(QWidget):
    """圆角方块：点击后填色 + 打勾动画，动画结束发 toggled。"""

    toggled = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.setCursor(Qt.PointingHandCursor)
        self._p = 0.0
        self._hover = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self._on_finished)

    def _get_p(self) -> float:
        return self._p

    def _set_p(self, v: float) -> None:
        self._p = float(v)
        self.update()

    p = Property(float, _get_p, _set_p)

    def _on_value(self, v) -> None:
        self._p = float(v)
        self.update()

    def _on_finished(self) -> None:
        if self._p >= 0.999:
            self.toggled.emit(True)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if ANIM:
                self._anim.stop()
                self._anim.setStartValue(self._p)
                self._anim.setEndValue(1.0)
                self._anim.start()
            else:
                self._p = 1.0
                self.update()
                self.toggled.emit(True)
        super().mousePressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        border = lerp(QColor(255, 255, 255, 95), ACCENT, self._p)
        if self._hover and self._p < 0.05:
            border = QColor(255, 255, 255, 150)
        fill = lerp(QColor(255, 255, 255, 12), ACCENT, self._p)
        p.setPen(QPen(border, 1.2))
        p.setBrush(fill)
        p.drawRoundedRect(r, 4.5, 4.5)

        if self._p > 0.05:  # 打勾：随进度画出来
            pen = QPen(QColor(12, 18, 14, int(235 * min(1.0, self._p * 1.4))), 1.9)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            path = QPainterPath(QPointF(4.2, 8.4))
            path.lineTo(6.9, 11.1)
            path.lineTo(11.9, 5.2)
            p.setOpacity(min(1.0, self._p * 1.2))
            p.drawPath(path)
            p.setOpacity(1.0)


class PulseDot(QWidget):
    """呼吸小圆点：active 时循环明暗，静止时半亮。"""

    def __init__(self, color: QColor, size: int = 7, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._color = QColor(color)
        self._a = 0.55
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.25)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1300)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._on_value)

    def _on_value(self, v) -> None:
        self._a = float(v)
        self.update()

    def set_active(self, on: bool) -> None:
        if on and ANIM:
            if self._anim.state() != QVariantAnimation.Running:
                self._anim.start()
        else:
            self._anim.stop()
            self._a = 0.75 if not on else 0.75
            self.update()

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self._color)
        c.setAlpha(int(255 * self._a))
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(QRectF(0, 0, self.width(), self.height()))


class Pill(QLabel):
    """计数徽章。"""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("pill")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(18)


# --------------------------------------------------------------------------- #
# 任务行（自绘背景 + 悬停动画 + 展开/收起动画）
# --------------------------------------------------------------------------- #
class TaskRow(QFrame):
    checked = Signal(dict)

    def __init__(self, task: dict, now: datetime, animate: bool = False,
                 delay: int = 0, parent=None) -> None:
        super().__init__(parent)
        self.task = task
        self.setObjectName("row")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.setAttribute(Qt.WA_Hover, True)
        self._hover = 0.0
        self._dead = False

        stamp, overdue = due_label(task, now)
        prio = int(task.get("priority") or 0)
        self._accent = QColor(RED if overdue else (AMBER if prio >= 3 else BLUE))
        if prio >= 3 and not overdue:
            self._accent = QColor(AMBER)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 5, 8, 5)
        lay.setSpacing(8)

        self.check = AnimCheck()
        self.check.toggled.connect(self._on_check)

        text = html.escape(task["title"])
        # 颜色写死在 HTML 里：富文本走调色板，深色面板上会变成看不见的深灰
        markup = f"<span style='color:{TXT}'>{text}</span>"
        if prio >= 3:
            markup += f" &nbsp;<span style='color:{AMBER.css_hue if False else '#f2b544'}'>●</span>"
        if stamp:
            col = "#ff6b6b" if overdue else DIM
            markup += f"&nbsp;&nbsp;<span style='color:{col}'>{stamp}</span>"
        self.label = QLabel(markup)
        self.label.setTextFormat(Qt.RichText)
        self.label.setWordWrap(True)
        self.label.setStyleSheet("font-size: 12px; background: transparent;")

        lay.addWidget(self.check, 0, Qt.AlignTop)
        lay.addWidget(self.label, 1, Qt.AlignTop)

        # 逾期呼吸点
        if overdue:
            self.dot = PulseDot(RED, 6)
            self.dot.set_active(True)
            lay.addWidget(self.dot, 0, Qt.AlignTop)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_hover)

        if animate and ANIM:
            self._entrance(delay)

    # -- 悬停 ---------------------------------------------------------------- #
    def _get_hover(self) -> float:
        return self._hover

    def _set_hover(self, v: float) -> None:
        self._hover = float(v)
        self.update()

    hover = Property(float, _get_hover, _set_hover)

    def _on_hover(self, v) -> None:
        self._hover = float(v)
        self.update()

    def enterEvent(self, e):
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(1.0)
        self._anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(0.0)
        self._anim.start()
        super().leaveEvent(e)

    # -- 进入动画：透明度 + 高度展开 ------------------------------------------ #
    def _entrance(self, delay: int) -> None:
        self.setMaximumHeight(0)
        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(0.0)
        self.setGraphicsEffect(eff)
        QTimer.singleShot(delay, lambda: self._play_entrance(eff))

    def _play_entrance(self, eff: QGraphicsOpacityEffect) -> None:
        if self._dead:
            return
        target = max(24, self.sizeHint().height())
        h = QPropertyAnimation(self, b"maximumHeight", self)
        h.setDuration(260)
        h.setStartValue(0)
        h.setEndValue(target)
        h.setEasingCurve(QEasingCurve.OutCubic)
        o = QPropertyAnimation(eff, b"opacity", self)
        o.setDuration(300)
        o.setStartValue(0.0)
        o.setEndValue(1.0)
        o.setEasingCurve(QEasingCurve.OutCubic)

        def done():
            if self._dead:
                return
            self.setMaximumHeight(16777215)
            self.setGraphicsEffect(None)

        h.finished.connect(done)
        h.start(QPropertyAnimation.DeleteWhenStopped)
        o.start(QPropertyAnimation.DeleteWhenStopped)
        self._anims = (h, o)

    # -- 勾选：填色动画结束 → 整行收起 ---------------------------------------- #
    def _on_check(self, checked: bool) -> None:
        if not checked or self._dead:
            return
        self._dead = True
        self.checked.emit(self.task)
        if not ANIM:
            self.setVisible(False)
            return
        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(1.0)
        self.setGraphicsEffect(eff)
        h = QPropertyAnimation(self, b"maximumHeight", self)
        h.setDuration(220)
        h.setStartValue(self.height())
        h.setEndValue(0)
        h.setEasingCurve(QEasingCurve.InCubic)
        o = QPropertyAnimation(eff, b"opacity", self)
        o.setDuration(200)
        o.setStartValue(1.0)
        o.setEndValue(0.0)
        h.finished.connect(lambda: self.setVisible(False))
        h.start(QPropertyAnimation.DeleteWhenStopped)
        o.start(QPropertyAnimation.DeleteWhenStopped)
        self._anims = (h, o)

    # -- 自绘 ---------------------------------------------------------------- #
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        h = self._hover
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, int(4 + 13 * h)))
        p.drawRoundedRect(r, 8, 8)

        # 左侧优先级 / 逾期色条
        c = QColor(self._accent)
        c.setAlpha(int(70 + 175 * h))
        p.setBrush(c)
        p.drawRoundedRect(QRectF(r.left() + 1.5, r.top() + 6, 2.4, max(6.0, r.height() - 12)), 1.2, 1.2)

        if h > 0.02:  # 悬停描边
            p.setPen(QPen(QColor(255, 255, 255, int(24 * h)), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 8, 8)


# --------------------------------------------------------------------------- #
# 挂件窗口
# --------------------------------------------------------------------------- #
class Widget(QWidget):
    def __init__(self, api: Vikunja, now_provider=datetime.now) -> None:
        super().__init__()
        self.api = api
        self.settings = QSettings("Hermes", "TaskBoard")
        self.tasks: list[dict] = []
        self.locked = False
        self._sig: tuple = ()
        self._seen: set[int] = set()

        # ---- 桌面挂件三件套：无边框 / 不进任务栏 / 永远沉底 ----
        self.setWindowFlags(
            Qt.FramelessWindowHint          # 无标题栏
            | Qt.Tool                       # 不进任务栏、不出现在 Alt-Tab
            | Qt.WindowStaysOnBottomHint    # 置底：永不盖住其他软件
        )
        self.setAttribute(Qt.WA_TranslucentBackground)   # 圆角外透明
        self.setAttribute(Qt.WA_ShowWithoutActivating)   # 显示时不抢焦点
        self.resize(int(self.settings.value("w", 760)), int(self.settings.value("h", 452)))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)   # 给投影留空间
        outer.setSpacing(0)

        panel = QFrame(self)
        panel.setObjectName("panel")
        panel.setStyleSheet(
            "QWidget { background: transparent; }"
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "#panel {"
            " background: qlineargradient(x1:0, y1:0, x2:0.9, y2:1,"
            "   stop:0 rgba(30, 36, 49, 246), stop:0.55 rgba(21, 25, 34, 244),"
            "   stop:1 rgba(15, 18, 25, 246));"
            " border-radius: 18px;"
            " border: 1px solid rgba(255, 255, 255, 30);"
            "}"
            "QLabel { color: #eef2f8; background: transparent; }"
            "QLabel#dim { color: #8b93a3; }"
            "QLabel#hint { color: #6b7484; }"
            "QLabel#pill { color: #8fd6ac; background: rgba(76, 159, 112, 40);"
            "  border-radius: 9px; padding: 1px 7px; font-size: 10px; }"
            "QLineEdit { background: rgba(255, 255, 255, 14); border: 1px solid rgba(255,255,255,22);"
            "  border-radius: 9px; padding: 6px 10px; color: #eef2f8;"
            "  selection-background-color: rgba(76,159,112,120); }"
            "QLineEdit:focus { border: 1px solid rgba(76, 159, 112, 190);"
            "  background: rgba(255, 255, 255, 22); }"
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 2px 0 2px 2px; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,38); border-radius: 3px;"
            "  min-height: 26px; }"
            "QScrollBar::handle:vertical:hover { background: rgba(255,255,255,70); }"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }"
            "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }"
        )
        # 投影：让挂件从壁纸上浮起来
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(46)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 165))
        panel.setGraphicsEffect(shadow)
        outer.addWidget(panel)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 13, 16, 11)
        lay.setSpacing(9)

        # ---- 顶栏 ----
        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("我的一天")
        title.setFont(QFont(title.font().family(), 13, QFont.DemiBold))
        self.count = Pill("")
        self.add_box = QLineEdit()
        self.add_box.setPlaceholderText("一句话添加：明天 9点 交作业 !高")
        self.add_box.setFixedWidth(272)
        self.add_box.setClearButtonEnabled(True)
        self.add_box.returnPressed.connect(self.on_add)
        head.addWidget(title)
        head.addWidget(self.count)
        head.addStretch(1)
        head.addWidget(self.add_box)
        lay.addLayout(head)

        # 顶部高光分隔线
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
                           " stop:0 rgba(255,255,255,0), stop:0.5 rgba(255,255,255,30),"
                           " stop:1 rgba(255,255,255,0));")
        lay.addWidget(line)

        # ---- 三列 ----
        cols = QHBoxLayout()
        cols.setSpacing(16)
        self.bodies: dict[str, QVBoxLayout] = {}
        self.scrolls: dict[str, QScrollArea] = {}
        self.col_pills: dict[str, Pill] = {}
        for key, name in COLUMNS:
            col = QVBoxLayout()
            col.setSpacing(5)
            ch = QHBoxLayout()
            ch.setSpacing(6)
            dot = PulseDot(ACCENT if key == "today" else QColor(255, 255, 255, 60), 6)
            dot.set_active(False)
            lbl = QLabel(name)
            lbl.setObjectName("dim")
            lbl.setStyleSheet("font-size: 11px; letter-spacing: 1px;")
            pill = Pill("0")
            self.col_pills[key] = pill
            ch.addWidget(dot)
            ch.addWidget(lbl)
            ch.addStretch(1)
            ch.addWidget(pill)
            col.addLayout(ch)

            body = QVBoxLayout()
            body.setSpacing(3)
            holder = QWidget()
            holder.setLayout(body)
            holder.setAttribute(Qt.WA_TranslucentBackground)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(holder)
            scroll.viewport().setAutoFillBackground(False)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setStyleSheet("background: transparent; border: none;")
            col.addWidget(scroll, 1)
            self.bodies[key] = body
            self.scrolls[key] = scroll
            cols.addLayout(col, 1)
        lay.addLayout(cols, 1)

        # ---- 状态栏 ----
        foot = QHBoxLayout()
        foot.setSpacing(6)
        self.status_dot = PulseDot(ACCENT, 6)
        self.status_dot.set_active(False)
        self.status = QLabel("")
        self.status.setObjectName("dim")
        self.status.setStyleSheet("font-size: 10px;")
        hint = QLabel("拖动移动 · 托盘锁定")
        hint.setObjectName("hint")
        hint.setStyleSheet("font-size: 10px;")
        foot.addWidget(self.status_dot)
        foot.addWidget(self.status)
        foot.addStretch(1)
        foot.addWidget(hint)
        lay.addLayout(foot)

        # ---- 拖动 ----
        self._drag_off: QPoint | None = None

    # -- 拖动 --------------------------------------------------------------- #
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.locked:
            self._drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_off is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_off)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_off = None
        self.settings.setValue("pos", self.pos())
        super().mouseReleaseEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.settings.setValue("w", self.width())
        self.settings.setValue("h", self.height())

    # -- 渲染 --------------------------------------------------------------- #
    def clear(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def render(self, force_anim: bool | None = None) -> None:
        now = self.now()
        groups: dict[str, list[dict]] = {k: [] for k, _ in COLUMNS}
        for t in self.tasks:
            groups[bucket(t, now)].append(t)

        sig = tuple((t["id"], t.get("due_date"), t["title"], t.get("priority"))
                    for t in sorted(self.tasks, key=lambda x: x["id"]))
        if sig == self._sig and force_anim is None:
            self.count.setText(f"{len(self.tasks)} 项")
            return
        first = not self._seen
        self._sig = sig
        ids = {t["id"] for t in self.tasks}

        for key, _ in COLUMNS:
            body = self.bodies[key]
            keep_scroll = self.scrolls[key].verticalScrollBar().value()
            self.clear(body)
            rows = sorted(groups[key], key=lambda x: x.get("due_date") or "9999")
            self.col_pills[key].setText(str(len(rows)))
            for i, t in enumerate(rows):
                new = t["id"] not in self._seen
                row = TaskRow(t, now, animate=(first or new or bool(force_anim)),
                              delay=min(i * 26, 260))
                row.checked.connect(self.on_done)
                body.addWidget(row)
            if not rows:
                empty = QLabel("—")
                empty.setObjectName("hint")
                empty.setStyleSheet("font-size: 11px; padding: 2px 2px;")
                body.addWidget(empty)
            body.addStretch(1)
            QTimer.singleShot(0, lambda s=self.scrolls[key], v=keep_scroll:
                              s.verticalScrollBar().setValue(v))

        self._seen = ids
        self.count.setText(f"{len(self.tasks)} 项")

    def now(self) -> datetime:
        return datetime.now(TZ)

    # -- 交互 --------------------------------------------------------------- #
    def on_done(self, task: dict) -> None:
        if not self.api.set_done(task, True):
            self.status.setText(self.api.last_error)
            self.status_dot.set_color(RED)
            return
        self.tasks = [t for t in self.tasks if t["id"] != task["id"]]
        self._sig = ()
        self.count.setText(f"{len(self.tasks)} 项")
        self.status.setText("已完成 1 项")
        self.status_dot.set_color(ACCENT)

    def on_add(self) -> None:
        raw = self.add_box.text().strip()
        if not raw:
            return
        title, due, prio = parse(raw, self.now())
        if self.api.add(title, due, prio):
            self.add_box.clear()
            self.status.setText(f"已添加：{title}")
            self.status_dot.set_color(ACCENT)
            self.refresh()
        else:
            self.status.setText(self.api.last_error)
            self.status_dot.set_color(RED)

    def refresh(self) -> None:
        self.status.setText("同步中…")
        self.status_dot.set_color(BLUE)
        self.status_dot.set_active(True)
        QApplication.processEvents()
        data = self.api.tasks()
        self.status_dot.set_active(False)
        if data is None:
            self.status.setText(self.api.last_error or "离线，10 秒后重试")
            self.status_dot.set_color(RED)
            return
        self.tasks = data
        self.status_dot.set_color(ACCENT)
        self.status.setText(f"已同步 {datetime.now(TZ):%H:%M:%S}")
        self.render()

    # -- 锁定（点击穿透，纯展示） -------------------------------------------- #
    def set_locked(self, locked: bool) -> None:
        self.locked = locked
        self.setAttribute(Qt.WA_TransparentForMouseEvents, locked)
        if sys.platform == "win32":
            self._win_click_through(locked)
        self.status.setText("已锁定（点击穿透）" if locked else "已解锁")

    def _win_click_through(self, on: bool) -> None:
        try:
            import ctypes
            hwnd = int(self.winId())
            GWL_EXSTYLE, WS_EX_TRANSPARENT, WS_EX_LAYERED = -20, 0x20, 0x80000
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = style | WS_EX_TRANSPARENT | WS_EX_LAYERED if on else style & ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    def set_opacity(self, value: float) -> None:
        self.setWindowOpacity(value)
        self.settings.setValue("opacity", value)


# --------------------------------------------------------------------------- #
# 托盘 + 启动
# --------------------------------------------------------------------------- #
def make_icon() -> QIcon:
    pix = QPixmap(32, 32)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(ACCENT)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(3, 3, 26, 26), 8, 8)
    p.setPen(QPen(QColor("white"), 2.4))
    p.drawLine(9, 16, 23, 16)
    p.end()
    return QIcon(pix)


def run(selftest: bool = False) -> int:
    global ANIM
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(not selftest)
    if selftest:
        ANIM = False   # 自检要立刻看到成品，不等动画

    api = Vikunja(load_config())
    api.login()

    w = Widget(api)
    w.refresh()                       # 先拉一次真实数据（失败也不崩）
    w.move(w.settings.value("pos", QPoint(90, 90)))
    w.setWindowOpacity(float(w.settings.value("opacity", 0.97)))

    if selftest:
        print("flags frameless:", bool(w.windowFlags() & Qt.FramelessWindowHint))
        print("flags tool:", bool(w.windowFlags() & Qt.Tool))
        print("flags bottom:", bool(w.windowFlags() & Qt.WindowStaysOnBottomHint))
        print("translucent:", w.testAttribute(Qt.WA_TranslucentBackground))
        print("no-activate:", w.testAttribute(Qt.WA_ShowWithoutActivating))
        print("tasks loaded:", len(w.tasks), "| api error:", api.last_error or "-")
        w.show()
        app.processEvents()
        img = w.grab()
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selftest.png")
        img.save(out)
        print("screenshot:", out, img.width(), "x", img.height())
        for sample in ("明天 9点 交作业 !高", "周五 17:00 交实验报告", "周会 无日期"):
            print("parse", sample, "->", parse(sample, datetime(2026, 11, 19, 15, 30, tzinfo=TZ)))
        return 0

    tray = QSystemTrayIcon(make_icon(), app)
    menu = QMenu()
    act_lock = QAction("锁定（点击穿透）", menu, checkable=True)
    act_lock.toggled.connect(w.set_locked)
    menu.addAction(act_lock)
    op_menu = menu.addMenu("透明度")
    for label, val in (("100%", 1.0), ("97%", 0.97), ("85%", 0.85), ("70%", 0.70)):
        a = QAction(label, op_menu)
        a.triggered.connect(lambda _=False, v=val: w.set_opacity(v))
        op_menu.addAction(a)
    menu.addSeparator()
    act_refresh = QAction("立即刷新", menu)
    act_refresh.triggered.connect(w.refresh)
    menu.addAction(act_refresh)
    act_quit = QAction("退出", menu)
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.setToolTip("Hermes 任务挂件")
    tray.show()

    timer = QTimer()
    timer.timeout.connect(w.refresh)
    timer.start(REFRESH_MS)

    w.show()
    return app.exec()


if __name__ == "__main__":
    if "--no-anim" in sys.argv:
        ANIM = False
    raise SystemExit(run("--selftest" in sys.argv))
