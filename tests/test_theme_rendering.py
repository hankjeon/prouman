import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
if importlib.util.find_spec('PySide6') is None:
    QApplication = None
else:
    from PySide6.QtCore import QEvent, QItemSelectionModel, QSettings, Qt
    from PySide6.QtGui import QBrush, QColor, QPalette
    from PySide6.QtWidgets import (QAbstractItemView, QApplication, QLabel, QStyleFactory,
                                   QVBoxLayout, QWidget)
    from allocation import theme
    from allocation.editor import EditTable, OrderedPeopleList, PinListDelegate
    from allocation.gui import CopyTableView, WorkspaceWindow, severity_colors


def contrast(first, second):
    def luminance(color):
        values = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
        return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
    a, b = luminance(first), luminance(second)
    return (max(a, b) + .05) / (min(a, b) + .05)


@unittest.skipUnless(QApplication, 'PySide6가 설치되지 않아 GUI 테스트를 건너뜁니다.')
class ThemeRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.artifacts = Path(tempfile.gettempdir()) / 'opencode'
        cls.artifacts.mkdir(exist_ok=True)

    def colors_in(self, image, rect, color, tolerance=30):
        expected = QColor(color)
        count = 0
        for y in range(max(0, rect.top()), min(image.height(), rect.bottom() + 1)):
            for x in range(max(0, rect.left()), min(image.width(), rect.right() + 1)):
                pixel = image.pixelColor(x, y)
                if max(abs(pixel.red() - expected.red()), abs(pixel.green() - expected.green()),
                       abs(pixel.blue() - expected.blue())) <= tolerance:
                    count += 1
        return count

    def make_widgets(self):
        window = QWidget()
        if self.app.platformName() == 'windows':
            window.setAttribute(Qt.WA_DontShowOnScreen)
            window.setAttribute(Qt.WA_ShowWithoutActivating)
            window.setWindowFlag(Qt.WindowDoesNotAcceptFocus)
        layout = QVBoxLayout(window)
        people = OrderedPeopleList()
        people.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for name in ('Alice', 'Bobby', 'Carol', 'Error'):
            people.addItem(name)
        states = ('fixed', 'partial', 'unfixed', 'fixed')
        people.set_pin_provider(lambda row: (states[row], ''))
        people.item(3).setForeground(QBrush(theme.error_text()))
        people.item(3).setToolTip('오류: 합계 확인')
        people.setFixedHeight(150)
        layout.addWidget(people)
        result = CopyTableView()
        result.show_table(['등급', '내용'], [['ERROR', 'Error'], ['WARNING', 'Warning']], colors=severity_colors())
        result.setFixedHeight(100)
        layout.addWidget(result)
        plan = EditTable(['사업', '값'])
        plan.setRowCount(1)
        plan.set_cell(0, 0, 'A', problem='오류: 상한')
        plan.set_cell(0, 1, '0.9', computed=True)
        plan.setFixedHeight(65)
        layout.addWidget(plan)
        legend = QLabel('오류 범례')
        legend.setStyleSheet(theme.legend_stylesheet(theme.problem_color()))
        layout.addWidget(legend)
        window.resize(500, 400)
        theme.apply_window_theme(window)
        window.show()
        self.app.processEvents()
        return window, people, result, plan, legend

    def test_palette_contrast_pairs(self):
        for name, colors in [('light', theme.LIGHT), ('dark', theme.DARK)]:
            with self.subTest(theme=name):
                for foreground, background in [('text', 'surface'), ('text', 'window'),
                                                ('list_selection_text', 'list_selection'),
                                                ('error_text', 'list_selection'), ('warning_text', 'list_selection')]:
                    self.assertGreaterEqual(contrast(colors[foreground], colors[background]), 4.5)
                for key in ('pin_fixed', 'pin_partial', 'pin_unfixed', 'current_cell'):
                    self.assertGreaterEqual(contrast(colors[key], colors['list_selection']), 3.0)

    def test_actual_widgets_styles_states_and_capture(self):
        original_style = self.app.style().objectName()
        styles = [name for name in ('Fusion', 'Windows', 'windowsvista', 'windows11')
                  if name.lower() in {s.lower() for s in QStyleFactory.keys()}]
        self.assertIn('Fusion', styles)
        try:
            for style in styles:
                self.app.setStyle(style)
                for dark in (False, True):
                    with self.subTest(style=style, dark=dark), patch.object(theme, 'is_dark', return_value=dark), \
                            patch.object(theme, 'high_contrast_active', return_value=False):
                        window, people, result, plan, legend = self.make_widgets()
                        self.addCleanup(window.deleteLater)
                        colors = theme.DARK if dark else theme.LIGHT
                        for selected in (False, True):
                            for focused in (False, True):
                                for active in (False, True):
                                    if self.app.platformName() != 'windows':
                                        self.app.setActiveWindow(window if active else None)
                                    for row in range(4):
                                        people.item(row).setSelected(selected)
                                    if focused and self.app.platformName() != 'windows':
                                        people.setFocus(Qt.OtherFocusReason)
                                    else:
                                        people.clearFocus()
                                    self.app.processEvents()
                                    image = people.viewport().grab().toImage()
                                    for row, state in enumerate(('fixed', 'partial', 'unfixed')):
                                        rect = people.visualItemRect(people.item(row))
                                        background = colors['list_selection'] if selected else colors['surface']
                                        self.assertEqual(image.pixelColor(rect.right() - 5, rect.center().y()).name(), background.lower())
                                        pin = theme.pin_icon_rect(rect)
                                        self.assertGreater(self.colors_in(image, pin, colors['pin_' + state], 45), 0)
                                        name_rect = rect.adjusted(30, 0, -250, 0)
                                        self.assertGreater(self.colors_in(image, name_rect,
                                            colors['list_selection_text'] if selected else colors['text'], 55), 0)
                        people.item(3).setSelected(True)
                        image = people.viewport().grab().toImage()
                        error_rect = people.visualItemRect(people.item(3))
                        self.assertGreater(self.colors_in(image, error_rect.adjusted(28, 0, -250, 0), colors['error_text'], 55), 0)
                        self.assertTrue(people.item(3).toolTip())
                        result.selectRow(0)
                        plan.setCurrentCell(0, 0)
                        plan.item(0, 0).setSelected(True)
                        self.app.processEvents()
                        for view in (result, plan):
                            image = view.viewport().grab().toImage()
                            rect = view.visualRect(view.model().index(0, 0))
                            self.assertGreater(self.colors_in(image, rect, colors['error_text'], 35), 0)
                            self.assertGreater(self.colors_in(image, rect, colors['list_selection'], 10), 0)
                        self.assertIn('background-color', legend.styleSheet())
                        image_path = self.artifacts / f'prouman-theme-{self.app.platformName()}-{style}-{("dark" if dark else "light")}.png'
                        self.assertTrue(window.grab().save(str(image_path)))
                        window.close()
        finally:
            self.app.setStyle(original_style)

    def test_runtime_palette_and_high_contrast(self):
        original = QPalette(self.app.palette())
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        try:
            w = WorkspaceWindow(QSettings(str(Path(temp.name) / 'settings.ini'), QSettings.IniFormat))
            if self.app.platformName() == 'windows':
                w.setAttribute(Qt.WA_DontShowOnScreen)
                w.setAttribute(Qt.WA_ShowWithoutActivating)
                w.setWindowFlag(Qt.WindowDoesNotAcceptFocus)
            w.show()
            self.addCleanup(w.deleteLater)
            dark = QPalette(original)
            dark.setColor(QPalette.Window, QColor('#1f1f1f'))
            self.app.setPalette(dark)
            self.app.processEvents()
            self.assertEqual(w.palette().color(QPalette.Base).name(), theme.DARK['surface'].lower())
            with patch.object(theme, 'high_contrast_active', return_value=True):
                w.apply_theme_styles()
                self.assertEqual(w.palette().color(QPalette.Highlight), self.app.palette().color(QPalette.Highlight))
                self.assertEqual(w.styleSheet(), '')
            w.set_font_size(24, save=False)
            self.assertEqual(w.font().pointSize(), 24)
        finally:
            self.app.setPalette(original)
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
