"""资源/数据基路径：兼容源码运行与 PyInstaller 冻结。"""
import os
import sys

# 只读资源根：随包分发的内置文件（backgrounds/ 等）
# 源码运行 = 项目根；PyInstaller 冻结 = 解包目录 _MEIPASS
RESOURCE_DIR = getattr(sys, "_MEIPASS", None) or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))

# 可写数据根：config.json、用户背景模板等（便携，跟 exe/项目目录走）
DATA_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else RESOURCE_DIR
