"""Desktop entry point, also used by the portable launchers."""

import sys


def main():
    from PySide6.QtCore import QLocale
    from PySide6.QtWidgets import QApplication
    from .window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    QLocale.setDefault(QLocale(QLocale.Language.Swedish, QLocale.Country.Sweden))
    app.setApplicationName("Lastkomb")
    app.setOrganizationName("Lastkomb")
    app.setStyle("Fusion")
    project = next((p for p in sys.argv[1:] if p.lower().endswith(".lastkomb")), None)
    window = MainWindow(project)
    screen = app.primaryScreen()
    if screen:
        bounds = screen.availableGeometry()
        window.resize(min(1440, bounds.width() - 28), min(880, bounds.height() - 45))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
