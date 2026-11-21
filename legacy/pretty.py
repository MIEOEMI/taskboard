"""pretty.py —— 界面美化参考版。

架构与 main.py 完全一致（set_tasklist + Lock + QTimer 1s 同步），
只重做「外观层」。对照 main.py 看 5 个带【美化】/【修正】标记的差异。

运行：uv run pretty.py
"""
from sys import argv, exit as sys_exit
from threading import Lock

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QApplication, QLabel, QVBoxLayout, QWidget)

tasklist = []
tasklist_lock = Lock()


def set_tasklist(tasks):
    with tasklist_lock:
        tasklist[:] = tasks


# 【美化 1】QSS 样式表：Qt 版的 CSS。main.py 没有任何样式，
# 控件全是系统默认灰 —— 这就是「不好看」的最大原因。
QSS = """
#card {
    background-color: rgba(17, 21, 27, 205);   /* 半透明深色卡片 */
    border-radius: 16px;                       /* 圆角 */
}
#title {
    color: #ffffff;
    font-size: 15px;
    font-weight: 600;
    padding: 4px 6px 10px 6px;
}
#task {
    color: #d7dee8;
    font-size: 14px;
    padding: 8px 6px 8px 12px;
    border-left: 3px solid #0d99ff;            /* 每行左侧蓝色小竖条 */
    border-bottom: 1px solid rgba(255, 255, 255, 20);
}
"""


class TaskCard(QWidget):
    """半透明圆角卡片：窗口本身全透明，背景由卡片的 QSS 来画。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        # 经典坑：普通 QWidget 想让 QSS 的 background 生效，必须开这个属性
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(340)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(0)

        self.title = QLabel("我的任务")
        self.title.setObjectName("title")
        outer.addWidget(self.title)

        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(0)
        outer.addLayout(self.rows_box)

        self.rows = []  # 复用 QLabel，思路和 main.py 的 labels 一样

    def refresh(self, tasks):
        self.title.setText(f"我的任务 · {len(tasks)}")

        # 任务变多就补 QLabel，变少就隐藏（复用，不重复创建）
        need = max(len(tasks), 1)
        while len(self.rows) < need:
            lbl = QLabel()
            lbl.setObjectName("task")
            self.rows_box.addWidget(lbl)
            self.rows.append(lbl)
        for lbl in self.rows:
            lbl.setVisible(False)

        if tasks:
            for i, text in enumerate(tasks):
                self.rows[i].setText(text)
                self.rows[i].setVisible(True)
        else:
            self.rows[0].setText("暂无任务")
            self.rows[0].setVisible(True)

        # 【美化 2】用了布局后，窗口高度可以随内容自适应
        self.adjustSize()


class WidgetWindow(QWidget):
    def __init__(self):
        super().__init__()
        # 【修正】main.py 用的是 WindowStaysOnTopHint —— 那是「置顶」，
        # 会盖住其他软件，和我们的需求正好相反。
        # 需求：置底 + 无边框 + 不进任务栏 → 三个 flag 组合：
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnBottomHint
            | Qt.Tool
        )
        # 【美化 3】窗口全透明，圆角由 TaskCard 的 QSS 呈现
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.card = TaskCard()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.card)

        self._drag_offset = None

    # 【美化 4】无边框窗口没有标题栏，自己实现拖动：
    # 按下时记住「鼠标相对窗口左上角的偏移」，移动时让窗口跟着走
    def mousePressEvent(self, e):
        self._drag_offset = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag_offset)

    def mouseReleaseEvent(self, e):
        self._drag_offset = None


def QtWindow():
    app = QApplication(argv)
    app.setStyleSheet(QSS)

    win = WidgetWindow()
    win.move(1800, 100)
    win.show()

    def update_list():
        with tasklist_lock:
            tasks = list(tasklist)
        win.card.refresh(tasks)

    timer = QTimer(win)
    timer.timeout.connect(update_list)
    timer.start(1000)
    update_list()  # 先立即刷一次，避免开屏空白

    sys_exit(app.exec_())


if __name__ == "__main__":
    set_tasklist(["高数作业", "背单词 ⚠高", "买火车票", "看论文", "整理笔记"])
    QtWindow()
