from calibre.gui2 import error_dialog, info_dialog
from calibre.utils.config import JSONConfig
from qt.core import QComboBox, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QPushButton, QWidget

PREFS = JSONConfig("plugins/rsvpnano_send")
PREFS.defaults["host"] = "http://192.168.4.1"
PREFS.defaults["send_mode"] = "rsvp"


class ConfigWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        

        self.host = QLineEdit(self)
        self.host.setText(PREFS['host'])
        self.test_btn = QPushButton("Test connection", self)
        self.test_btn.clicked.connect(self.test_connection)
        host_row = QHBoxLayout()
        host_row.addWidget(self.host, 1)
        host_row.addWidget(self.test_btn)
        notice = QLabel("Host is the address of the reader. From the RSVP Nano open 'Companion sync' and it will display its current IP address assigned by the router. The default IP address when joined to its own Wi-Fi is 'http://192.168.4.1'.")
        notice.setWordWrap(True)
        notice.setStyleSheet('color: gray; font-style: italic;')

        self.mode = QComboBox(self)
        self.mode.addItem("RSVP (converted from EPUB by the plugin before transfer)", "rsvp")
        self.mode.addItem("TXT (converted by the RSVP Nano after transfer)", "txt")
        self.mode.setCurrentIndex(max(0, self.mode.findData(PREFS["send_mode"])))

        layout.addRow('Host', host_row)
        layout.addRow('', notice)
        layout.addRow('Conversion', self.mode)

    def test_connection(self):
        from calibre_plugins.rsvpnano_send.rsvp import ping

        try:
            info = ping(self.host.text())
        except Exception as e:
            return error_dialog(self, "Connection failed", str(e), show=True)
        info_dialog(
            self, "Connected",
            "Reader %s\nFirmware %s" % (info.get("ssid", "?"), info.get("firmwareVersion", "?")),
            show=True,
        )

    def save_settings(self):
        PREFS["host"] = self.host.text().strip()
        PREFS["send_mode"] = self.mode.currentData()
