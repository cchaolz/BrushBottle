import sys
import os
import subprocess
import cv2
import numpy as np
import pyautogui
import win32gui
import win32con
import time
import logging
import win32ui
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QLabel, QTextEdit, QComboBox, QInputDialog,
                               QMessageBox, QListWidgetItem,
                               QGroupBox, QListWidget, QDialog, QDialogButtonBox, QCheckBox, QLineEdit)
from PySide6.QtCore import QRect, QSettings, Qt, QTimer, Signal, QThread
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QImage, QIcon

from PIL import Image
from mouse import send_input_click, send_input_rclick
import ctypes

# PrintWindow 标志: 读取 GPU/DirectX 渲染内容
PW_RENDERFULLCONTENT = 0x00000002


def is_packaged():
    """是否处于打包后的运行环境(PyInstaller / Nuitka)"""
    return getattr(sys, 'frozen', False) or '__compiled__' in globals()


PACKAGED = is_packaged()


def get_base_dir():
    """程序根目录: 打包后为 exe 所在目录, 否则为脚本所在目录"""
    if PACKAGED:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()


def get_resource_path(name):
    """资源路径: 优先 exe/脚本同级目录, 其次打包器内部目录(_MEIPASS)"""
    external = os.path.join(BASE_DIR, name)
    if os.path.exists(external):
        return external

    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        internal = os.path.join(meipass, name)
        if os.path.exists(internal):
            return internal

    return external


window = None
# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, "auto_pirate_team.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("AutoPirateTeam")


def find_window(title_keyword):
    """按标题查找窗口: 先精确匹配, 再匹配标题包含关键字(忽略大小写)"""
    hwnd = win32gui.FindWindow(None, title_keyword)
    if hwnd:
        return hwnd, win32gui.GetWindowText(hwnd)

    matched = []

    def enum_callback(h, _):
        if win32gui.IsWindowVisible(h):
            title = win32gui.GetWindowText(h)
            if title and title_keyword.lower() in title.lower():
                matched.append((h, title))
        return True

    win32gui.EnumWindows(enum_callback, None)
    return matched[0] if matched else (None, None)


class WindowSelector(QDialog):
    """窗口选择对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择游戏窗口")
        self.setMinimumWidth(400)
        self.setMinimumHeight(300)

        # 窗口列表
        self.window_list = QListWidget()
        self.window_info = {}  # 存储窗口信息: {索引: (hwnd, 标题)}

        # 按钮
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        # 刷新按钮
        self.refresh_button = QPushButton("刷新窗口列表")
        self.refresh_button.clicked.connect(self.refresh_window_list)

        # 布局
        layout = QVBoxLayout()
        layout.addWidget(QLabel("选择要操作的游戏窗口:"))
        layout.addWidget(self.window_list)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.button_box)
        self.setLayout(layout)

        # 初始加载窗口列表
        self.refresh_window_list()

    def refresh_window_list(self):
        """刷新窗口列表"""
        self.window_list.clear()
        self.window_info.clear()

        def enum_windows_callback(hwnd, results):
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
                title = win32gui.GetWindowText(hwnd)
                # 只显示标题非空的窗口
                if title.strip():
                    index = len(results)
                    results[index] = (hwnd, title)
                    self.window_list.addItem(f"{title} (hwnd: {hwnd})")
            return True

        win32gui.EnumWindows(enum_windows_callback, self.window_info)

    def get_selected_window(self):
        """获取选中的窗口句柄和标题"""
        current_index = self.window_list.currentRow()
        if current_index >= 0 and current_index in self.window_info:
            return self.window_info[current_index]
        return None, None


class CaptureLabel(QLabel):
    """显示游戏截图, 支持拖拽框选, 选中区域坐标以原图为准"""

    region_selected = Signal(int, int, int, int)  # x1, y1, x2, y2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 240)
        self._pixmap = None
        self._scale = 1.0
        self._offset = (0.0, 0.0)
        self._start = None
        self._end = None

    def set_image(self, pixmap):
        self._pixmap = pixmap
        self._start = None
        self._end = None
        self._update_geometry()
        self.update()

    def _update_geometry(self):
        """按控件大小等比缩放图片, 记录缩放比与居中偏移"""
        if self._pixmap is None:
            return

        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw <= 0 or ph <= 0:
            return

        self._scale = min(self.width() / pw, self.height() / ph)
        self._offset = ((self.width() - pw * self._scale) / 2,
                        (self.height() - ph * self._scale) / 2)

    def resizeEvent(self, event):
        self._update_geometry()
        super().resizeEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._pixmap is None:
            return

        painter = QPainter(self)
        painter.drawPixmap(
            int(self._offset[0]), int(self._offset[1]),
            int(self._pixmap.width() * self._scale), int(self._pixmap.height() * self._scale),
            self._pixmap)

        if self._start is not None and self._end is not None:
            painter.setPen(QPen(QColor(255, 64, 64), 2))
            painter.drawRect(QRect(self._start, self._end).normalized())

    def _to_image_pos(self, pos):
        """控件坐标 -> 原图坐标"""
        if self._pixmap is None or self._scale <= 0:
            return 0, 0

        x = (pos.x() - self._offset[0]) / self._scale
        y = (pos.y() - self._offset[1]) / self._scale
        x = max(0, min(int(round(x)), self._pixmap.width()))
        y = max(0, min(int(round(y)), self._pixmap.height()))
        return x, y

    def mousePressEvent(self, event):
        if self._pixmap is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self._start = event.pos()
        self._end = event.pos()
        self.update()

    def mouseMoveEvent(self, event):
        if self._start is None:
            return
        self._end = event.pos()
        self.update()

    def mouseReleaseEvent(self, event):
        if self._start is None or event.button() != Qt.MouseButton.LeftButton:
            return

        self._end = event.pos()
        x1, y1 = self._to_image_pos(self._start)
        x2, y2 = self._to_image_pos(self._end)
        self._start = None
        self._end = None
        self.update()

        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        if right - left >= 2 and bottom - top >= 2:
            self.region_selected.emit(left, top, right, bottom)


class TemplateCaptureDialog(QDialog):
    """模板截取对话框: 按所需模板清单, 从游戏截图截取贴图存为指定分辨率"""

    def __init__(self, game_window, template_engine, resolution, parent=None):
        super().__init__(parent)
        self.game_window = game_window
        self.template_engine = template_engine
        self.resolution = resolution
        self.target_dir = template_engine.resolution_dir(resolution)
        self.screenshot = None

        self.setWindowTitle(f"截取模板 - {resolution}")
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self.resize(1200, 720)
        os.makedirs(self.target_dir, exist_ok=True)

        # 左侧: 所需模板清单 + 参考贴图
        self.template_list = QListWidget()
        self.template_list.currentItemChanged.connect(self.on_template_selected)

        self.reference_label = QLabel("其他分辨率无此贴图")
        self.reference_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.reference_label.setMinimumHeight(160)
        self.reference_label.setStyleSheet("border: 1px solid #808080;")

        self.hint_label = QLabel("-")
        self.hint_label.setWordWrap(True)

        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel(f"所需模板（保存到 {resolution}）:"))
        left_layout.addWidget(self.template_list, 1)
        left_layout.addWidget(QLabel("参考贴图（其他分辨率）:"))
        left_layout.addWidget(self.reference_label)
        left_layout.addWidget(self.hint_label)

        # 右侧: 游戏截图
        self.capture_label = CaptureLabel()
        self.capture_label.region_selected.connect(self.save_region)

        self.refresh_button = QPushButton("刷新游戏截图")
        self.refresh_button.clicked.connect(self.refresh_screenshot)

        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.accept)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.close_button)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("在截图上拖拽框选贴图，松手即保存为左侧选中的模板"))
        right_layout.addWidget(self.capture_label, 1)
        right_layout.addLayout(button_layout)

        layout = QHBoxLayout()
        layout.addLayout(left_layout, 1)
        layout.addLayout(right_layout, 3)
        self.setLayout(layout)

        self.reload_template_list()
        self.refresh_screenshot()

    def current_template_name(self):
        item = self.template_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def select_template(self, name):
        for row in range(self.template_list.count()):
            item = self.template_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == name:
                self.template_list.setCurrentItem(item)
                return

    def reload_template_list(self):
        """刷新清单状态, 并自动选中第一个缺失的模板"""
        self.template_list.clear()
        first_missing = None

        for name in self.template_engine.list_template_names():
            path = os.path.join(self.target_dir, f"{name}.png")
            image = cv2.imread(path) if os.path.exists(path) else None

            if image is None:
                status = "缺失"
                if first_missing is None:
                    first_missing = name
            else:
                status = f"已截取 {image.shape[1]}x{image.shape[0]}"

            item = QListWidgetItem(f"{name}  [{status}]")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.template_list.addItem(item)

        if first_missing:
            self.select_template(first_missing)
        elif self.template_list.count():
            self.template_list.setCurrentRow(0)

    def on_template_selected(self, current, _previous=None):
        """选中模板后, 显示其他分辨率下的同名贴图作参考"""
        if current is None:
            return

        name = current.data(Qt.ItemDataRole.UserRole)
        for resolution in self.template_engine.list_resolutions():
            if resolution == self.resolution:
                continue

            path = os.path.join(self.template_engine.resolution_dir(resolution), f"{name}.png")
            if not os.path.exists(path):
                continue

            pixmap = QPixmap(path)
            if pixmap.isNull():
                continue

            self.reference_label.setPixmap(pixmap.scaled(
                260, 160, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            self.hint_label.setText(f"{name} 参考自 {resolution}: {pixmap.width()}x{pixmap.height()}")
            return

        self.reference_label.clear()
        self.reference_label.setText("其他分辨率无此贴图")
        self.hint_label.setText(name)

    def refresh_screenshot(self):
        """重新抓取游戏窗口截图"""
        screenshot = self.game_window.capture_screenshot()
        if screenshot is None:
            self.hint_label.setText("截图失败，请确认游戏窗口有效")
            return

        self.screenshot = screenshot
        h, w = screenshot.shape[:2]
        q_img = QImage(screenshot.data, w, h, 3 * w, QImage.Format.Format_RGB888).rgbSwapped()
        self.capture_label.set_image(QPixmap.fromImage(q_img))

    def save_region(self, x1, y1, x2, y2):
        """把框选区域存为当前选中模板对应的贴图"""
        name = self.current_template_name()
        if not name:
            QMessageBox.warning(self, "提示", "请先在左侧选择要保存的模板名")
            return

        if self.screenshot is None:
            return

        region = self.screenshot[y1:y2, x1:x2]
        if region.size == 0:
            return

        os.makedirs(self.target_dir, exist_ok=True)
        path = os.path.join(self.target_dir, f"{name}.png")
        cv2.imwrite(path, region)
        logger.info(f"已保存模板贴图: {path}, 尺寸: {x2 - x1}x{y2 - y1}")

        # 截取的正是当前使用的分辨率目录, 立即重载供主流程使用
        if self.template_engine.resolution == self.resolution:
            self.template_engine.load_all_templates()

        self.reload_template_list()


class GameWindow:
    """游戏窗口操作类"""

    def __init__(self, hwnd=None, title=None):
        """
        初始化游戏窗口操作类

        Args:
            hwnd: 窗口句柄
            title: 窗口标题
        """
        self.hwnd = hwnd
        self.title = title
        self.window_rect = None

        # 更新窗口位置信息
        self.update_window_position()

    def set_window(self, hwnd, title):
        """设置要操作的窗口"""
        self.hwnd = hwnd
        self.title = title
        self.update_window_position()

    def update_window_position(self):
        """更新游戏窗口位置"""
        if self.hwnd is None:
            self.window_rect = None
            return False

        try:
            # 获取窗口位置
            left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
            self.window_rect = (left, top, right, bottom)
            # logger.info(f"窗口位置: {self.window_rect}")
            return True
        except Exception as e:
            logger.error(f"获取窗口位置失败: {str(e)}")
            self.window_rect = None
            return False

    def get_window_rect(self):
        """获取窗口区域"""
        if self.window_rect is None:
            self.update_window_position()
        return self.window_rect

    def capture_screenshot(self):
        """捕获游戏窗口的截图"""
        if not self.hwnd:
            logger.info("未选择游戏窗口，请先查找窗口")
            return None

        try:
            # 获取窗口大小
            left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
            width = right - left
            height = bottom - top

            # 创建设备上下文
            hwnd_dc = win32gui.GetWindowDC(self.hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            # 创建位图对象
            save_bitmap = win32ui.CreateBitmap()
            save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
            save_dc.SelectObject(save_bitmap)

            def read_bitmap():
                info = save_bitmap.GetInfo()
                buf = save_bitmap.GetBitmapBits(True)
                return np.array(Image.frombuffer(
                    'RGB', (info['bmWidth'], info['bmHeight']),
                    buf, 'raw', 'BGRX', 0, 1))

            # GPU/DirectX 渲染的窗口用 BitBlt 只能拿到黑屏，必须用 PrintWindow
            printed = ctypes.windll.user32.PrintWindow(
                self.hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
            frame = read_bitmap()

            if not printed or not frame.any():
                # PrintWindow 无效时回退 BitBlt
                save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), win32con.SRCCOPY)
                frame = read_bitmap()

            # 转换为OpenCV格式
            screenshot = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            # 清理资源
            win32gui.DeleteObject(save_bitmap.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(self.hwnd, hwnd_dc)

            # 更新界面显示的截图
            h, w = screenshot.shape[:2]
            bytes_per_line = 3 * w
            q_img = QImage(screenshot.data, w, h, bytes_per_line, QImage.Format_RGB888).rgbSwapped()
            pixmap = QPixmap.fromImage(q_img)
            window.screenshot_update.emit(pixmap)

            return screenshot
        except Exception as e:
            logger.error(f"截图失败: {e}")
            return None

    def window_to_screen_position(self, window_pos):
        """将窗口内坐标转换为屏幕坐标"""
        if self.window_rect is None:
            logger.error("无法转换坐标: 窗口无效")
            return window_pos

        x, y = window_pos
        window_x, window_y, _, _ = self.window_rect
        return (window_x + x, window_y + y)

    def is_foreground(self):
        """检查窗口是否在前台"""
        if self.hwnd is None:
            return False

        try:
            # 获取当前前台窗口
            foreground_hwnd = win32gui.GetForegroundWindow()
            return foreground_hwnd == self.hwnd
        except:
            return False

    def set_foreground(self):
        """将窗口设置为前台窗口"""
        if self.hwnd is None:
            return False

        try:
            if self.is_foreground():
                return True

            # 如果窗口最小化，先恢复
            if win32gui.IsIconic(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)

            # 设为前台窗口
            win32gui.SetForegroundWindow(self.hwnd)
            time.sleep(0.1)  # 等待窗口前台化

            # 再次检查是否成功设置为前台
            if self.is_foreground():
                logger.info("成功将窗口设置为前台")
                return True
            else:
                logger.warning("设置窗口为前台失败")
                return False

        except Exception as e:
            logger.error(f"设置窗口前台失败: {str(e)}")
            return False

    def click(self, position, duration=0.1, delay=0.2, up=True):
        """点击指定位置"""
        if up and not self.set_foreground():
            logger.error("点击失败: 无法将窗口设为前台")
            return False
        send_input_click(self.hwnd, position[0], position[1])  # 可行，但是必须在前台
        # send_background_click(self.hwnd, position[0], position[1])
        time.sleep(delay)
        return True

    def drag(self, start_pos, end_pos, duration=0.2, delay=0.1):
        """从起点拖动到终点"""
        # if not self.set_foreground():
        #     logger.error("拖动失败: 无法将窗口设为前台")
        #     return False
        import win32api
        current_x, current_y = win32api.GetCursorPos()
        start_screen_pos = self.window_to_screen_position(start_pos)
        end_screen_pos = self.window_to_screen_position(end_pos)

        pyautogui.moveTo(start_screen_pos[0], start_screen_pos[1])
        pyautogui.mouseDown()
        pyautogui.moveTo(end_screen_pos[0], end_screen_pos[1], duration=duration)
        pyautogui.mouseUp()
        time.sleep(delay)
        win32api.SetCursorPos((current_x, current_y))
        return True

    def is_valid(self):
        """检查窗口是否有效"""
        if self.hwnd is None:
            return False

        try:
            return win32gui.IsWindow(self.hwnd)
        except:
            return False


class TemplateMatchingEngine:
    """模板匹配引擎类"""

    DEFAULT_RESOLUTION = "1366x768"

    def __init__(self, templates_dir=None, threshold=0.85):
        """
        初始化模板匹配引擎

        Args:
            templates_dir: 模板根目录(其下按分辨率分子目录)
            threshold: 匹配阈值
        """
        self.templates_root = templates_dir or get_resource_path("templates")
        self.threshold = threshold
        self.templates = {}
        self.template_dimensions = {}
        self.resolution = None
        self.templates_dir = self.templates_root

        # 确保模板根目录存在
        if not os.path.exists(self.templates_root):
            os.makedirs(self.templates_root)
            logger.info(f"创建模板目录: {self.templates_root}")

        resolution = self.default_resolution()
        if resolution:
            self.set_resolution(resolution)
        else:
            self.load_all_templates()

    def list_resolutions(self):
        """列出模板根目录下的分辨率子目录"""
        if not os.path.isdir(self.templates_root):
            return []

        return sorted(name for name in os.listdir(self.templates_root)
                      if os.path.isdir(os.path.join(self.templates_root, name)))

    def default_resolution(self):
        """默认分辨率: 优先 1366x768, 否则取第一个子目录; 无子目录返回 None"""
        names = self.list_resolutions()
        if self.DEFAULT_RESOLUTION in names:
            return self.DEFAULT_RESOLUTION
        return names[0] if names else None

    def set_resolution(self, resolution):
        """切换分辨率目录并重载模板"""
        self.resolution = resolution
        if resolution:
            self.templates_dir = os.path.join(self.templates_root, resolution)
        else:
            self.templates_dir = self.templates_root

        logger.info(f"切换模板目录: {self.templates_dir}")
        self.load_all_templates()

    def list_template_names(self):
        """所有分辨率目录下出现过的模板名(并集), 用于模板截取界面"""
        names = set()
        for resolution in self.list_resolutions():
            path = os.path.join(self.templates_root, resolution)
            for filename in os.listdir(path):
                if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    names.add(os.path.splitext(filename)[0])
        return sorted(names)

    def resolution_dir(self, resolution):
        return os.path.join(self.templates_root, resolution)

    def load_all_templates(self):
        """加载所有模板图片"""
        self.templates = {}
        self.template_dimensions = {}

        if not os.path.exists(self.templates_dir):
            logger.error(f"模板目录不存在: {self.templates_dir}")
            return

        logger.info("开始加载模板图片...")
        count = 0

        for filename in os.listdir(self.templates_dir):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                template_name = os.path.splitext(filename)[0]
                template_path = os.path.join(self.templates_dir, filename)

                try:
                    template = cv2.imread(template_path)
                    if template is None:
                        logger.error(f"无法加载模板: {template_path}")
                        continue

                    self.templates[template_name] = template
                    h, w = template.shape[:2]
                    self.template_dimensions[template_name] = (w, h)

                    logger.info(f"已加载模板: {template_name}, 尺寸: {w}x{h}")
                    count += 1
                except Exception as e:
                    logger.error(f"加载模板{filename}失败: {str(e)}")

        logger.info(f"共加载 {count} 个模板")

    def add_template(self, template_name, image):
        """添加或更新模板"""
        self.templates[template_name] = image
        h, w = image.shape[:2]
        self.template_dimensions[template_name] = (w, h)
        logger.info(f"已添加/更新模板: {template_name}, 尺寸: {w}x{h}")

    def save_template(self, template_name, image):
        """保存模板到文件"""
        if not os.path.exists(self.templates_dir):
            os.makedirs(self.templates_dir)

        template_path = os.path.join(self.templates_dir, f"{template_name}.png")
        cv2.imwrite(template_path, image)
        logger.info(f"已保存模板到: {template_path}")

    def find_template(self, template_name, screenshot=None, threshold=None, log2wnd=False):
        """
        在截图中查找指定模板

        Args:
            template_name: 模板名称
            screenshot: 截图
            threshold: 匹配阈值

        Returns:
            (是否找到, 位置(x,y), 匹配度)
        """
        if template_name not in self.templates:
            (log2wnd and window.add_log or logger.error)(f"模板不存在: {template_name}")
            return False, None, 0

        if screenshot is None or screenshot.size == 0:
            (log2wnd and window.add_log or logger.error)("截图无效")
            return False, None, 0

        if threshold is None:
            threshold = self.threshold

        template = self.templates[template_name]

        try:
            # 使用模板匹配
            result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

            # 如果匹配度超过阈值
            if max_val >= threshold:
                # 计算中心点位置
                w, h = self.template_dimensions[template_name]
                center_x = max_loc[0] + w // 2
                center_y = max_loc[1] + h // 2

                (log2wnd and window.add_log or logger.debug)(f"找到模板 {template_name}, 位置: ({center_x}, {center_y}), 匹配度: {max_val:.4f}")
                return True, (center_x, center_y), max_val
            else:
                (log2wnd and window.add_log or logger.debug)(f"未找到模板 {template_name}, 最大匹配度: {max_val:.4f}")
                return False, None, max_val
        except Exception as e:
            (log2wnd and window.add_log or logger.error)(f"查找模板 {template_name} 时出错: {str(e)}")
            return False, None, 0

    def find_all_templates(self, template_name, screenshot, threshold=None, max_results=10):
        """
        在截图中查找所有匹配的模板实例

        Args:
            template_name: 模板名称
            screenshot: 截图
            threshold: 匹配阈值
            max_results: 最大结果数量

        Returns:
            位置列表 [(pos_x, pos_y, score), ...]
        """
        if template_name not in self.templates:
            logger.error(f"模板不存在: {template_name}")
            return []

        if screenshot is None or screenshot.size == 0:
            logger.error("截图无效")
            return []

        if threshold is None:
            threshold = self.threshold

        template = self.templates[template_name]
        template_h, template_w = template.shape[:2]

        try:
            # 使用模板匹配
            result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)

            # 找到所有匹配点
            locations = np.where(result >= threshold)
            locations = list(zip(*locations[::-1]))  # 转换为(x, y)格式

            # 合并重叠的匹配结果
            matches = []
            used = set()

            for loc in locations:
                if len(matches) >= max_results:
                    break

                # 检查这个点是否已经处理过
                skip = False
                for used_loc in used:
                    if abs(loc[0] - used_loc[0]) < template_w // 2 and abs(loc[1] - used_loc[1]) < template_h // 2:
                        skip = True
                        break

                if skip:
                    continue

                # 添加到结果中
                center_x = loc[0] + template_w // 2
                center_y = loc[1] + template_h // 2
                score = result[loc[1], loc[0]]
                matches.append((center_x, center_y, score))
                used.add(loc)

            return matches
        except Exception as e:
            logger.error(f"查找所有模板 {template_name} 时出错: {str(e)}")
            return []


class AutoTeamPirate(QThread):
    """自动组队打海盗线程"""

    # 定义信号
    status_update = Signal(str)
    log_message = Signal(str)

    def __init__(self, template_engine, game_window):
        super().__init__()
        self.template_engine = template_engine
        self.game_window = game_window
        self.running = False
        self.paused = False
        self.count = 0
        self.idle_count = 0
        self.force_open_bottle = False
        self.m_nNotFoundBottleCount = 0

    def run(self):
        """运行自动组队打海盗流程"""
        self.running = True
        self.log_message.emit("开始自动组队打海盗流程")

        self.game_window.set_foreground()
        while self.running:
            try:
                if not self.paused:
                    # 检查窗口是否有效
                    if not self.game_window.is_valid():
                        self.log_message.emit("游戏窗口无效，请重新选择窗口")
                        self.stop()
                        continue

                    # 执行自动化流程
                    self.execute_team_pirate_workflow()

                # 短暂延迟
                time.sleep(0.2)
            except Exception as e:
                self.log_message.emit(f"错误: {str(e)}")
                time.sleep(5)  # 出错后等待一段时间

    def find_template(self, template_name, screenshot=None, click=False, log=True, threshold=None, delay=0.2, check_target_temp=None, try_count=1):
        bNewScreenShot = screenshot is None
        while try_count > 0:
            try_count -= 1
            # 截图
            if bNewScreenShot:
                screenshot = self.game_window.capture_screenshot()
                if screenshot is None:
                    self.log_message.emit("截图失败")
                    return False

            # 步骤1: 查找并点击图标
            self.status_update.emit(f"正在查找{template_name}图标...")
            found, position, score = self.template_engine.find_template(template_name, screenshot, threshold)

            if found:
                if log and not click:
                    self.log_message.emit(f"找到{template_name}图标，位置：{position}，匹配度：{score:.4f}")
                if click:
                    if log:
                        self.log_message.emit(f"点击{template_name}图标，位置：{position}，匹配度：{score:.4f}")

                    retry = 0
                    while retry < 5:
                        self.game_window.click(position)
                        time.sleep(delay)  # 等待界面切换
                        if check_target_temp is None or self.find_template(check_target_temp, threshold=threshold, log=False):
                            break

                        retry += 1
                        time.sleep(0.1)

                return True

            if try_count > 0:
                time.sleep(0.1)

        return False

    def back(self):
        if not self.find_template("return", click=True, log=False):
            send_input_rclick(self.game_window.hwnd)

    def execute_team_pirate_workflow(self):
        # 更新一下窗口位置
        self.game_window.update_window_position()

        # 如果窗口最小化，先恢复
        if win32gui.IsIconic(self.game_window.hwnd):
            win32gui.ShowWindow(self.game_window.hwnd, win32con.SW_RESTORE)

        """执行自动组队打海盗流程"""
        bAnySuccess = self.find_template("league", click=True) or self.find_template("empire", click=True)
        bAnySuccess = self.find_template("wars", click=True) or bAnySuccess
        bAnySuccess = self.process_join_teams() or bAnySuccess
        if not bAnySuccess:
            self.back()

        # 自动关闭解散队伍弹窗
        self.find_template("cancel", click=True)

        # 自动关闭通知消息
        if window.close_notice_checkbox.isChecked():
            self.find_template("closenotice", click=True)

        # 防卡死
        self.count += 1
        if self.count % 1000 == 0:
            self.log_message.emit("返回一下，防卡死")
            self.back()  # 返回一下避免卡死

        if self.force_open_bottle or self.idle_count >= 10:
            self.try_open_bottle()

        if bAnySuccess:
            time.sleep(1)

        # if self.game_window.is_foreground():
        #     self.game_window.click((0, -20), up=False)

    def try_open_bottle(self):
        self.idle_count = 0

        if not window.open_bottle_checkbox.isChecked() and not window.open_activity_bottle_checkbox.isChecked() and not self.force_open_bottle:
            return

        self.force_open_bottle = False

        if self.find_template("mycreateteam1", click=False, log=False, threshold=0.9) or self.find_template("mycreateteam2", click=False, log=False, threshold=0.9):
            return

        if self.check_any_team_free():
            self.log_message.emit("存在空闲队伍，开瓶子...")
            return self.use_bottle()

        self.log_message.emit("没有空闲队伍，不开瓶子...")
        return False

    def check_any_team_free(self):
        self.status_update.emit(f"校验是否有空闲的队伍...")
        bRet = False
        nRetry = 0
        while not bRet and nRetry < 5:
            self.back()
            bRet = self.find_template("teambtn", click=True, log=False, threshold=0.9)
            nRetry += 1

        if bRet:
            time.sleep(0.3)
            bRet = self.find_template("cfreeteam1", click=False, log=False, threshold=0.9) \
                   or self.find_template("cfreeteam2", click=False, log=False, threshold=0.8) \
                   or self.find_template("cfreeteam3", click=False, log=False, threshold=0.8) \
                   or self.find_template("cfreeteam4", click=False, log=False, threshold=0.8)

        return bRet

    def use_bottle(self):
        self.status_update.emit(f"尝试开瓶子...")
        bRet = False
        # 进背包
        retry = 0
        while not bRet and retry < 2:
            self.back()
            self.find_template("unfold1", click=True, log=False, threshold=0.95) or self.find_template("unfold2", click=True, log=False, threshold=0.95, try_count=2)
            bRet = self.find_template("bag", click=True, log=False, threshold=0.95, check_target_temp="bag_activity", try_count=2)
            retry += 1

        # 进活动背包
        if bRet:
            self.log_message.emit("成功进入背包...")
            bRet = self.find_template("bag_activity", click=True, log=False, delay=0.5, threshold=0.95, check_target_temp="bag_activity_done", try_count=3)

        # 找到并点击瓶子
        if bRet:
            self.log_message.emit("成功进入活动背包...")
            szBottleName = "bottle_activity" if window.open_activity_bottle_checkbox.isChecked() else "bottle"
            self.game_window.drag((550, 450), (550, 50), duration=0.5, delay=0.2)
            bRet = self.find_template(szBottleName, click=True, log=False, threshold=0.95)
            if not bRet:
                self.game_window.drag((550, 450), (550, 50), duration=0.5, delay=0.2)
                bRet = self.find_template(szBottleName, click=True, log=False, threshold=0.95)

        # 使用瓶子
        if bRet:
            self.m_nNotFoundBottleCount = 0
            self.log_message.emit("找到瓶子...")
            bRet = self.find_template("use_item", click=True, log=False, threshold=0.95)
            if bRet:
                self.log_message.emit("使用瓶子...")
        else:
            self.m_nNotFoundBottleCount += 1
            if self.m_nNotFoundBottleCount >= 5:
                self.m_nNotFoundBottleCount = 0
                window.open_bottle_checkbox.setChecked(False)
                window.open_activity_bottle_checkbox.setChecked(False)
                self.log_message.emit("连续5次未找到瓶子，关闭开瓶子选项...")

        retry = 0
        while (bRet and retry == 0) or (not bRet and 0 < retry < 5):
            bRet = self.process_create_team(template="gofighting2", check_target_temp="create_team")
            if bRet:
                self.log_message.emit("组队进攻...")
            bRet = self.process_create_team(template="create_team")
            if bRet:
                self.log_message.emit("发起组队...")
            bRet = self.process_go_fight()

            retry += 1

        return bRet

    def process_create_team(self, retry=5, template="create_team", threshold=0.9, check_target_temp=None):
        # 发起组队
        bRet = False
        nRetry = 0
        while not bRet and nRetry < retry:
            bRet = self.find_template(template, click=True, log=False, threshold=threshold, check_target_temp=check_target_temp)
            nRetry += 1
            time.sleep(0.1)

        return bRet

    def process_go_fight(self):
        # 出征
        bRet = self.select_free_team(["freeteam3", "freeteam4"], drag_delay=1)
        nRetry = 0
        while not bRet and nRetry < 5:
            bRet = self.select_free_team(["freeteam3", "freeteam4"], drag_delay=1)
            nRetry += 1
            time.sleep(0.1)

        return bRet

    def process_join_teams(self):
        """处理加入队伍流程"""
        screenshot = self.game_window.capture_screenshot()

        # 队伍数量已达设定值，本次不操作
        nTeamCount = int(window.team_count_combo.currentText())
        if len(self.template_engine.find_all_templates("inteam", screenshot)) >= nTeamCount:
            self.log_message.emit(f"当前队伍数量已达 {nTeamCount}，本次不操作")
            return True

        # 查找所有可加入的队伍
        self.status_update.emit("正在查找可加入的队伍...")
        matches = self.template_engine.find_all_templates("canjoin", screenshot, threshold=0.8)

        if not matches:
            # self.log_message.emit("未找到可加入的队伍")
            bInWarsWindow = self.find_template("inwars", log=False)
            if bInWarsWindow:
                inteam_matches = self.template_engine.find_all_templates("inteam", screenshot)
                if len(inteam_matches) < nTeamCount:
                    self.idle_count += 1
            return bInWarsWindow

        self.log_message.emit(f"找到 {len(matches)} 个可能可加入的队伍")

        bSuccess = False
        # 遍历所有可能的队伍
        for pos_x, pos_y, score in matches:
            self.status_update.emit(f"正在检查位置 ({pos_x}, {pos_y}) 的队伍...")

            # 点击该位置
            self.game_window.click((pos_x, pos_y))
            time.sleep(0.1)

            # 检查是否是海盗队伍
            screenshot = self.game_window.capture_screenshot()
            found, _, _ = self.template_engine.find_template("haidao", screenshot)
            if not found and window.join_warriors_checkbox.isChecked():
                found, _, _ = self.template_engine.find_template("xianfeng", screenshot)

            if not found:
                if window.join_warriors_checkbox.isChecked():
                    self.log_message.emit("不是海盗/蛮族先锋队伍，继续检查下一个")
                else:
                    self.log_message.emit("不是海盗队伍，继续检查下一个")
                self.back()
                continue

            # 检查是否在组队中
            # 假设我们在特定区域内检查"组队中"文字
            found = not self.find_template("fighting", screenshot, threshold=0.9) and \
                    not self.find_template("waiting", screenshot, threshold=0.9) and \
                    not self.find_template("going", screenshot, threshold=0.9)
            if not found:
                self.log_message.emit("不是组队中的队伍，继续检查下一个")
                self.back()
                continue

            # 查找加入按钮
            found, join_pos, _ = self.template_engine.find_template("join", screenshot)

            if found:
                self.log_message.emit(f"找到可加入的海盗队伍，点击加入按钮")
                self.game_window.click(join_pos)
                time.sleep(0.5)

                # 进入步骤4：选择空闲队伍
                bSuccess = self.select_free_team() or bSuccess
            else:
                self.log_message.emit("未找到加入按钮，继续检查下一个")
                self.back()

        return bSuccess

    def select_free_team(self, listFreeTemplate=None, bFirstTime=True, drag_delay=0.1):
        """选择空闲的队伍位置"""
        if listFreeTemplate is None:
            listFreeTemplate = ["freeteam1", "freeteam3", "freeteam2", "freeteam4"]

        self.status_update.emit("正在查找空闲队伍位置...")
        screenshot = self.game_window.capture_screenshot()

        free_team_found = False
        for template_name in listFreeTemplate:
            found, position, fix = self.template_engine.find_template(template_name, screenshot, threshold=0.95)

            if found:
                self.log_message.emit(f"找到空闲队伍位置: {template_name}, 匹配度: {fix:.4f}")
                free_team_found = True

                retry = 0
                while retry < 3:
                    if template_name != "freeteam1" and template_name != "freeteam3": # 已经是选中的队伍，不用再选
                        # 点击偏移位置
                        offset_pos = (position[0] - 50, position[1] + 50)
                        self.game_window.click(offset_pos)
                        time.sleep(0.01)

                    # 查找并点击确认加入按钮
                    if self.find_template("gojoin", click=True):
                        self.log_message.emit("找到确认加入按钮，点击加入")
                        time.sleep(0.01)  # 等待加入完成
                        if not self.find_template("gojoin"):
                            self.back()
                            return True
                    else:
                        self.log_message.emit("未找到确认加入按钮")
                        retry += 1

        if bFirstTime and not free_team_found and self.find_template("gojoin"):
            self.log_message.emit("未找到空闲队伍位置，尝试拖动列表")

            # 拖动列表查找更多队伍
            if self.game_window.window_rect:
                window_x, window_y, window_right, window_bottom = self.game_window.window_rect
                window_w = window_right - window_x
                window_h = window_bottom - window_y

                # 计算拖动的起点和终点
                start_x = window_w // 2
                start_y = window_h - 50
                end_x = start_x - 300
                end_y = start_y

                self.log_message.emit(f"拖动列表: ({start_x}, {start_y}) -> ({end_x}, {end_y})")
                self.game_window.drag((start_x, start_y), (end_x, end_y), delay=drag_delay)

                # 递归调用自己继续查找
                return self.select_free_team(bFirstTime=False)

        return False

    def stop(self):
        """停止线程"""
        self.running = False
        self.wait()

    def pause(self):
        """暂停流程"""
        self.paused = True
        self.status_update.emit("已暂停")

    def resume(self):
        """恢复流程"""
        self.paused = False
        self.status_update.emit("已恢复")


class MainWindow(QMainWindow):
    """主窗口类"""

    screenshot_update = Signal(QPixmap)

    def __init__(self, monitor_titles=("AoE", "云重返帝国")):
        super().__init__()
        self.settings = QSettings("BrushBottle", "AutoPirateTeam")
        self.template_engine = TemplateMatchingEngine()
        self.game_window = GameWindow()
        self.auto_thread = None

        self.load_settings()
        self.init_ui()
        self.init_window(monitor_titles)

    def load_settings(self):
        """恢复上次选择的模板目录"""
        resolution = self.settings.value("templates/resolution", "")
        if resolution and resolution in self.template_engine.list_resolutions():
            self.template_engine.set_resolution(resolution)

    def init_window(self, monitor_titles):
        try:
            for keyword in monitor_titles:
                game_hwnd, title = find_window(keyword)
                if game_hwnd:
                    self.game_window.set_window(game_hwnd, title)
                    self.start_button.setEnabled(True)
                    self.current_window_label.setText(f"{title} (hwnd: {game_hwnd})")
                    self.add_log(f"已自动选择窗口: {title} (hwnd: {game_hwnd})")
                    return

            self.add_log(f"未找到窗口 {'/'.join(monitor_titles)}，请手动选择")
            self.select_game_window()
        except Exception as e:
            print(f"查找窗口时发生错误: {e}")
            return None

    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle("帝国刷瓶子助手")
        self.setGeometry(100, 100, 1000, 800)

        # 主布局
        main_layout = QHBoxLayout()

        # 左侧控制区域
        control_panel = QGroupBox("控制面板")
        control_layout = QVBoxLayout()

        # 窗口选择按钮
        window_layout = QHBoxLayout()
        self.select_window_button = QPushButton("选择游戏窗口")
        self.select_window_button.clicked.connect(self.select_game_window)
        window_layout.addWidget(self.select_window_button)

        self.current_window_label = QLabel("未选择窗口")
        window_layout.addWidget(self.current_window_label, 1)

        control_layout.addLayout(window_layout)

        # 开始按钮
        self.start_button = QPushButton("开始 (Ctrl+F6)")
        self.start_button.clicked.connect(self.start_automation)
        self.start_button.setEnabled(False)  # 初始禁用
        control_layout.addWidget(self.start_button)

        # 停止按钮
        self.stop_button = QPushButton("停止 (Ctrl+F8)")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_automation)
        control_layout.addWidget(self.stop_button)

        window_layout = QHBoxLayout()
        control_layout.addLayout(window_layout)
        # 单选框（自动关闭通知消息）
        self.close_notice_checkbox = QCheckBox("自动关闭通知消息")
        self.close_notice_checkbox.setChecked(True)
        window_layout.addWidget(self.close_notice_checkbox)
        # 单选框（自动加入蛮族先锋）
        self.join_warriors_checkbox = QCheckBox("自动加入蛮族先锋")
        self.join_warriors_checkbox.setChecked(True)
        window_layout.addWidget(self.join_warriors_checkbox)

        window_layout = QHBoxLayout()
        control_layout.addLayout(window_layout)
        # 单选框（自动开瓶子）
        self.open_bottle_checkbox = QCheckBox("自动开瓶子")
        self.open_bottle_checkbox.setChecked(False)
        self.open_bottle_checkbox.stateChanged.connect(self.on_open_bottle_checkbox)
        window_layout.addWidget(self.open_bottle_checkbox)

        # 单选框（自动开活动瓶子）
        self.open_activity_bottle_checkbox = QCheckBox("自动开活动瓶子")
        self.open_activity_bottle_checkbox.setChecked(False)
        self.open_activity_bottle_checkbox.stateChanged.connect(self.on_open_activity_bottle_checkbox)
        window_layout.addWidget(self.open_activity_bottle_checkbox)

        # 当前队伍数量选择框
        window_layout = QHBoxLayout()
        window_layout.addWidget(QLabel("当前队伍数量（1~5）"))
        self.team_count_combo = QComboBox()
        self.team_count_combo.addItems(["1", "2", "3", "4", "5"])
        self.team_count_combo.setCurrentText("5")
        window_layout.addWidget(self.team_count_combo, 1)
        control_layout.addLayout(window_layout)

        window_layout = QHBoxLayout()
        # 模板测试按钮
        self.test_template_button = QPushButton("测试模板")
        self.test_template_button.clicked.connect(self.test_template)
        window_layout.addWidget(self.test_template_button)
        self.test_template_edit = QLineEdit("")
        window_layout.addWidget(self.test_template_edit)
        control_layout.addLayout(window_layout)

        # 开瓶子按钮
        self.open_bottle_button = QPushButton("开瓶子")
        self.open_bottle_button.clicked.connect(self.open_bottle)
        control_layout.addWidget(self.open_bottle_button)

        # 状态显示
        status_group = QGroupBox("状态")
        status_layout = QVBoxLayout()
        self.status_label = QLabel("就绪")
        status_layout.addWidget(self.status_label)
        status_group.setLayout(status_layout)
        control_layout.addWidget(status_group)

        # 模板管理区
        template_group = QGroupBox("模板管理")
        template_layout = QVBoxLayout()

        resolution_layout = QHBoxLayout()
        resolution_layout.addWidget(QLabel("模板目录:"))
        self.resolution_combo = QComboBox()
        self.resolution_combo.currentTextChanged.connect(self.on_resolution_changed)
        resolution_layout.addWidget(self.resolution_combo, 1)
        template_layout.addLayout(resolution_layout)

        template_button_layout = QHBoxLayout()
        self.capture_template_button = QPushButton("新增模板")
        self.capture_template_button.clicked.connect(self.capture_new_template)
        template_button_layout.addWidget(self.capture_template_button)

        self.browse_templates_button = QPushButton("浏览模板")
        self.browse_templates_button.clicked.connect(self.browse_templates)
        template_button_layout.addWidget(self.browse_templates_button)

        template_layout.addLayout(template_button_layout)
        template_group.setLayout(template_layout)
        control_layout.addWidget(template_group)

        # 日志区域
        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        control_layout.addWidget(log_group, 1)

        control_panel.setLayout(control_layout)
        main_layout.addWidget(control_panel, 1)

        # 右侧截图区域
        screen_panel = QGroupBox("当前画面")
        screen_layout = QVBoxLayout()
        self.screen_label = QLabel()
        self.screen_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        screen_layout.addWidget(self.screen_label)
        screen_panel.setLayout(screen_layout)
        main_layout.addWidget(screen_panel, 2)

        # 设置中央窗口部件
        central_widget = QWidget()
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        self.screenshot_update.connect(self.update_screenshot)

        # 注册全局热键
        self.register_hotkeys()

        # 模板分辨率下拉框
        self.refresh_resolution_combo()

        # 添加日志信息
        self.add_log("程序已启动")
        self.add_log(f"模板目录 {self.template_engine.resolution}，已加载 {len(self.template_engine.templates)} 个模板")

    def register_hotkeys(self):
        try:
            # 注册 Ctrl+F6 为开始热键
            win32gui.RegisterHotKey(int(self.winId()), 1, win32con.MOD_CONTROL, win32con.VK_F6)
            # 注册 Ctrl+F8 为停止热键
            win32gui.RegisterHotKey(int(self.winId()), 3, win32con.MOD_CONTROL, win32con.VK_F8)
        except Exception as e:
            self.add_log(f"注册热键失败: {e}")
            pass

    def nativeEvent(self, eventType, message):
        if eventType == "windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == win32con.WM_HOTKEY:
                if msg.wParam == 1:  # Ctrl+F6
                    self.start_automation()
                    return True, 0
                elif msg.wParam == 3:  # Ctrl+F8
                    self.stop_automation()
                    return True, 0
        return super().nativeEvent(eventType, message)

    def closeEvent(self, event):
        # 注销热键
        for hotkey_id in (1, 3):
            win32gui.UnregisterHotKey(int(self.winId()), hotkey_id)
        super().closeEvent(event)

    def select_game_window(self):
        """选择游戏窗口"""
        dialog = WindowSelector(self)
        result = dialog.exec()

        if result == QDialog.DialogCode.Accepted:
            hwnd, title = dialog.get_selected_window()
            if hwnd:
                self.game_window.set_window(hwnd, title)
                self.current_window_label.setText(f"已选择: {title}")
                self.start_button.setEnabled(True)
                self.add_log(f"已选择窗口: {title} (hwnd: {hwnd})")

    def add_log(self, message):
        """添加日志信息"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")
        self.log_text.ensureCursorVisible()

    def update_status(self, status):
        """更新状态显示"""
        self.status_label.setText(status)

    def update_screenshot(self, pixmap):
        """更新截图显示"""
        scaled_pixmap = pixmap.scaled(
            self.screen_label.width(),
            self.screen_label.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.screen_label.setPixmap(scaled_pixmap)

    def start_automation(self):
        """开始自动化流程"""
        if not self.game_window.is_valid():
            self.add_log("游戏窗口无效，请重新选择窗口")
            return

        if self.auto_thread is None or not self.auto_thread.isRunning():
            self.auto_thread = AutoTeamPirate(self.template_engine, self.game_window)
            self.auto_thread.status_update.connect(self.update_status)
            self.auto_thread.log_message.connect(self.add_log)
            self.auto_thread.start()

            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.add_log("自动化流程已启动")

    def stop_automation(self):
        """停止自动化流程"""
        if self.auto_thread and self.auto_thread.isRunning():
            self.auto_thread.stop()
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.add_log("自动化流程已停止")

    def test_template(self):
        """测试当前模板"""
        screenshot = self.game_window.capture_screenshot()  # 先截个屏

        template_name = self.test_template_edit.text()
        if not template_name:
            self.add_log("请输入要测试的模板名称")
            return

        if template_name not in self.template_engine.templates:
            self.add_log(f"模板 '{template_name}' 不存在")
            return

        if screenshot is not None:
            self.template_engine.find_template(template_name, screenshot, log2wnd=True)

    def open_bottle(self):
        self.auto_thread.force_open_bottle = True

    def refresh_resolution_combo(self):
        """刷新模板目录下拉框"""
        combo = self.resolution_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(self.template_engine.list_resolutions())
        if self.template_engine.resolution:
            combo.setCurrentText(self.template_engine.resolution)
        combo.blockSignals(False)

    def on_resolution_changed(self, resolution):
        """切换模板目录并重载模板"""
        if not resolution or resolution == self.template_engine.resolution:
            return

        self.template_engine.set_resolution(resolution)
        self.settings.setValue("templates/resolution", resolution)
        self.add_log(f"已切换模板目录 {resolution}，加载 {len(self.template_engine.templates)} 个模板")

    def capture_new_template(self):
        """新增分辨率模板目录, 并进入截取贴图的界面"""
        if not self.game_window.is_valid():
            self.add_log("游戏窗口无效，请先选择窗口")
            return

        resolution, accepted = QInputDialog.getText(
            self, "新增模板", "模板名称（如 1920x1080）:")
        if not accepted:
            return

        resolution = resolution.strip()
        if not resolution or os.sep in resolution or '/' in resolution:
            self.add_log(f"模板名称非法: {resolution}")
            return

        os.makedirs(self.template_engine.resolution_dir(resolution), exist_ok=True)
        self.add_log(f"开始截取模板: {resolution}")

        dialog = TemplateCaptureDialog(self.game_window, self.template_engine, resolution, self)
        dialog.exec()

        target_dir = self.template_engine.resolution_dir(resolution)
        saved = len([f for f in os.listdir(target_dir)
                     if f.lower().endswith(('.png', '.jpg', '.jpeg'))])
        total = len(self.template_engine.list_template_names())
        self.add_log(f"{resolution} 截取结束: {saved}/{total}")

        self.refresh_resolution_combo()

    def browse_templates(self):
        """浏览已有模板"""
        template_dir = os.path.abspath(self.template_engine.templates_root)
        if os.path.exists(template_dir):
            if sys.platform == 'win32':
                os.startfile(template_dir)
            else:
                import subprocess
                subprocess.Popen(['xdg-open', template_dir])
            self.add_log(f"已打开模板目录: {template_dir}")
        else:
            self.add_log(f"模板目录不存在: {template_dir}")

    def on_open_bottle_checkbox(self, state):
        if state == Qt.CheckState.Checked:
            self.open_activity_bottle_checkbox.setChecked(False)

    def on_open_activity_bottle_checkbox(self, state):
        if state == Qt.CheckState.Checked:
            self.open_bottle_checkbox.setChecked(False)

    def closeEvent(self, event):
        """关闭窗口事件处理"""
        if self.auto_thread and self.auto_thread.isRunning():
            self.auto_thread.stop()
            self.auto_thread.wait()
        event.accept()


def is_admin():
    """当前是否以管理员权限运行"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_as_admin():
    """以管理员权限重新启动自身"""
    executable = sys.executable
    if PACKAGED:
        args = sys.argv[1:]
    else:
        args = [os.path.abspath(__file__)] + sys.argv[1:]

    params = subprocess.list2cmdline(args)
    try:
        # 返回码 <= 32 表示失败（例如用户取消了 UAC 提示）
        # 显式传入程序目录，保证提权后 templates、日志路径一致
        return ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, params, BASE_DIR, 1) > 32
    except Exception as e:
        logger.error(f"请求管理员权限失败: {e}")
        return False


if __name__ == "__main__":
    if not is_admin():
        if run_as_admin():
            sys.exit(0)
        logger.warning("未获得管理员权限，继续以普通权限运行")

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(get_resource_path("icon.png")))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
