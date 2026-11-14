#建一个Qt窗口


from PyQt5.QtWidgets import QApplication, QWidget, QLabel
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
import threading
if __name__ == "__main__":
    tasklist = ['s', 't', 'u']
    app = QApplication([]) # 创建QApplication实例
    window = QWidget() # 创建QWidget实例
    
    window.setWindowTitle("My Qt Window") # 设置窗口标题
    window.resize(600, 700) # 设置窗口大小
    window.move(1800, 100) # 设置窗口位置
    window.lower() # 窗口置底
    

    #window.setAttribute(Qt.WA_TranslucentBackground) # 设置窗口背景半透明
    
    #忽略win+d并去掉窗口边框
    window.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint) # 窗口置顶并去掉窗口边框
    window.show() # 显示窗口
    #编写显示列表函数，#在窗口显示任务列表
    #在窗口显示列表而非终端输出
    def show_list():
        distance = 30
        size = 20 #字体大小
        y = 10
        for task in tasklist:
            label = QLabel(task, window)
            label.setFont(QFont("Arial", size))
            label.move(10, y)
            label.show()
            y += distance

    #threading.Thread(target=show_list).start()   出现报错QObject::setParent: Cannot set parent, new parent is in a different thread
    show_list()
    app.exec_() # 进入事件循环
    
