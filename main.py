#建一个Qt窗口
from PyQt5.QtWidgets import QApplication, QWidget

if __name__ == "__main__":
    app = QApplication([]) # 创建QApplication实例
    window = QWidget() # 创建QWidget实例
    window.show() # 显示窗口
    app.exec_() # 进入事件循环