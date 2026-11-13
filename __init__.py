from calibre.customize import InterfaceActionBase


class RSVPNanoSendPlugin(InterfaceActionBase):
    name = "RSVP Nano Send"
    description = "Send and optionally convert selected books to an RSVP Nano reader over Wi-Fi"
    supported_platforms = ["windows", "osx", "linux"]
    author = "Alex Camilleri"
    version = (0, 1, 2)
    minimum_calibre_version = (6, 0, 0)

    actual_plugin = "calibre_plugins.rsvpnano_send.ui:RSVPNanoSendAction"

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.rsvpnano_send.config import ConfigWidget

        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()
