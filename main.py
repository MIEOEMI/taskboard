
# Lock 用来保护 tasklist，避免其他线程更新列表时，界面线程同时读取。
from threading import Lock

# Qt 控件只能在 Qt 主线程中创建和修改。
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication, QLabel, QWidget


# 这是数据，不是界面控件。其他代码通过 set_tasklist 更新它。
tasklist = []
tasklist_lock = Lock()


def set_tasklist(tasks):
    """用新任务列表替换旧列表，供其他线程安全调用。"""
    with tasklist_lock:
        # 切片赋值是“原地替换”，不会让 tasklist 这个共享对象失效。
        tasklist[:] = tasks


def QtWindow():
    # QApplication 负责 Qt 应用和事件循环，必须在主线程创建。
    app = QApplication([])
    window = QWidget()
    window.resize(600, 700)
    window.move(1800, 100)
    window.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)

    # 保存已经创建的标签，后续刷新时尽量复用，而不是重复创建。
    labels = []

    def update_list():
        # 只在锁内复制数据，避免占用锁进行界面操作。
        with tasklist_lock:
            tasks = list(tasklist)

        # 任务变多时补充 QLabel。
        while len(labels) < len(tasks):
            label = QLabel(window)
            label.setFont(QFont("Arial", 20))
            label.show()
            labels.append(label)

        # 任务变少时删除多余 QLabel，避免旧任务继续显示。
        while len(labels) > len(tasks):
            labels.pop().deleteLater()

        # 更新文字和位置。任务数量相同时只修改文字，不创建新控件。
        for index, task in enumerate(tasks):
            labels[index].setText(task)
            labels[index].move(10, 10 + index * 60)

        # 根据内容调整窗口尺寸。
        #window.adjustSize()

    window.show()
    # 先立即显示一次，避免窗口打开时等待第一个定时器周期。
    update_list()

    # Qt 事件循环每隔 1000 毫秒发出 timeout 信号。
    # 信号连接到 update_list 后，界面会持续同步 tasklist。
    timer = QTimer(window)
    timer.timeout.connect(update_list)
    timer.start(1000)

    # 进入事件循环；窗口事件和 QTimer 都依赖它运行。
    app.exec_()


if __name__ == "__main__":
    # 示例数据。实际项目中可由网络请求或其他线程调用 set_tasklist。
    set_tasklist(["s", "t", "u"])
    QtWindow()
    
