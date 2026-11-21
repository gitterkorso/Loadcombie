def stylesheet(dark):
    bg, panel, words, quiet, border, field, hover, selected = (
        ("#111b23", "#19242e", "#eef4f7", "#a4b8c8", "#364653", "#202f3b", "#263b49", "#214c4b")
        if dark
        else ("#eef2f5", "#ffffff", "#203143", "#52697c", "#ced9e0", "#ffffff", "#f1f6f8", "#dcf2ec")
    )
    return f"""
* {{ font-family: "Inter","Segoe UI","Helvetica Neue",sans-serif; font-size: 12px; }}
QMainWindow,QDialog {{ background:{bg}; }}
QWidget {{ color:{words}; }}
QWidget#workspace, QScrollArea, QScrollArea > QWidget > QWidget, QTabWidget::pane,
QTextBrowser, QHeaderView, QMenu, QTreeWidget, QTableView, QGroupBox, QWidget#panel {{ background:{panel}; }}
QMenu {{ border:1px solid {border}; padding:4px; }}
QMenu::item {{ padding:6px 11px; }}
QMenu::item:selected, QComboBox QAbstractItemView::item:selected {{ background:{selected}; }}
QWidget#bar {{ background:#142d3c; }}
QLabel#brand {{ color:white; font-size:22px; font-weight:700; }}
QLabel#subbrand {{ color:#bdcfd7; font-size:12px; }}
QLabel#title {{ font-size:21px; font-weight:600; }}
QLabel#heading {{ font-size:15px; font-weight:600; }}
QLabel#quiet {{ color:{quiet}; }}
QLabel#warning {{ background:{"#403323" if dark else "#fff3da"}; color:{"#ffdeb0" if dark else "#754912"}; border-radius:4px; padding:6px; }}
QLabel#badge {{ color:{"#9cdfcc" if dark else "#175e52"}; background:{selected}; border-radius:4px; padding:4px 8px; }}
QPushButton,QToolButton,QComboBox,QLineEdit,QDoubleSpinBox {{ background:{field}; border:1px solid {border}; border-radius:4px; padding:5px 7px; }}
QPushButton:hover,QToolButton:hover {{ background:{hover}; }}
QPushButton#primary {{ background:#147668; color:white; border:1px solid #126759; font-weight:600; }}
QWidget#bar QPushButton {{ background:#294654; color:#f1f7f8; border:1px solid #486272; padding:6px 10px; }}
QWidget#bar QPushButton#primary {{ background:#277e70; border-color:#399684; }}
QPushButton:disabled,QToolButton:disabled {{ color:{quiet}; }}
QTabWidget::pane,QGroupBox {{border:1px solid {border};border-radius:3px;}}
QGroupBox {{ margin-top:10px; padding:6px; }} QGroupBox::title {{ subcontrol-origin:margin;left:8px; }}
QTabBar::tab {{ background:{hover}; color:{quiet}; padding:7px 10px; }} QTabBar::tab:selected {{background:{panel};color:{words};border-bottom:2px solid #208676;}}
QTreeWidget,QTableView {{border:none; gridline-color:{border}; alternate-background-color:{hover};selection-background-color:{selected};selection-color:{words};}}
QTreeWidget::item,QTableWidget::item {{ padding:3px; }}
QHeaderView::section {{ background:{hover}; border:none; border-bottom:1px solid {border}; padding:5px; color:{quiet}; }}
QSplitter::handle {{ background:{border}; }}
QStatusBar {{background:{panel};color:{quiet};}}
QToolTip {{ background:{panel};color:{words};border:1px solid {border}; }}
"""
