import os
import tempfile

from calibre.gui2 import Dispatcher, error_dialog, info_dialog
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.threaded_jobs import ThreadedJob
from qt.core import QMenu

from calibre_plugins.rsvpnano_send.config import PREFS

_CONVERT_PREFERENCE = ["AZW3", "KEPUB", "MOBI", "FB2", "DOCX", "HTMLZ", "ODT", "RTF", "PDF"]


def _pick_source(fmts, tmp):
    if "EPUB" in fmts:
        return "EPUB", fmts["EPUB"]
    if "TXT" in fmts:
        return "TXT", fmts["TXT"]
    src = None
    for f in _CONVERT_PREFERENCE:
        if f in fmts:
            src = fmts[f]
            break
    if src is None:
        src = next(iter(fmts.values()))
    from calibre.ebooks.conversion.plumber import Plumber
    from calibre.utils.logging import Log

    out = os.path.join(tmp, "converted.epub")
    Plumber(src, out, Log(), report_progress=lambda *a, **k: None).run()
    return "EPUB", out


def _pick_txt(fmts, tmp):
    if "TXT" in fmts:
        return fmts["TXT"]
    fmt, src = _pick_source(fmts, tmp)  # EPUB (converted if necessary)
    from calibre.ebooks.conversion.plumber import Plumber
    from calibre.utils.logging import Log

    out = os.path.join(tmp, "converted.txt")
    Plumber(src, out, Log(), report_progress=lambda *a, **k: None).run()
    return out


def send_job(base_url, category, mode, books, notifications=None, abort=None, log=None):
    from calibre_plugins.rsvpnano_send import rsvp

    info = rsvp.ping(base_url)
    results = []
    total = len(books)
    with tempfile.TemporaryDirectory(prefix="rsvpnano_") as tmp:
        for i, b in enumerate(books):
            if abort is not None and abort.is_set():
                break
            frac = i / float(total)
            try:
                base = rsvp.safe_name(b["title"], b["author"], b["id"])
                notifications.put((frac, "Converting " + b["title"]))
                if mode == "txt":
                    with open(_pick_txt(b["formats"], tmp), "rb") as f:
                        data = f.read()
                    name, detail = base + ".txt", "plain text"
                else:
                    fmt, path = _pick_source(b["formats"], tmp)
                    data, words = rsvp.convert_to_rsvp(
                        path, fmt, b["title"], b["author"], source=base + ".epub"
                    )
                    name, detail = base + ".rsvp", "%d words" % words
                notifications.put((frac, "Uploading " + b["title"]))
                rsvp.upload(base_url, category, name, data)
                results.append((b["title"], True, "%s (%s, %d KB)" % (name, detail, len(data) // 1024)))
            except Exception as e:
                results.append((b["title"], False, str(e)))
    return {"reader": info, "results": results}


class RSVPNanoSendAction(InterfaceAction):
    name = "RSVP Nano Send"
    action_spec = (
        "Send to RSVP Nano",
        None,
        "Send selected books to an RSVP Nano reader over Wi-Fi",
        None,
    )
    action_type = "current"

    def genesis(self):
        icon = get_icons('images/icon.png', 'RSVP Nano Send')
        self.qaction.setIcon(icon)
        self.qaction.triggered.connect(lambda *a: self.send("book"))
        self.menu = QMenu(self.gui)
        self.qaction.setMenu(self.menu)
        self.create_menu_action(
            self.menu, "rsvpnano_send_book", "Send selected as book",
            triggered=lambda *a: self.send("book"),
        )
        self.create_menu_action(
            self.menu, "rsvpnano_send_article", "Send selected as article",
            triggered=lambda *a: self.send("article"),
        )
        self.menu.addSeparator()
        self.create_menu_action(
            self.menu, "rsvpnano_send_config", "Configure...",
            triggered=self.configure,
        )

    def configure(self):
        self.interface_action_base_plugin.do_user_config(self.gui)

    def send(self, category):
        ids = list(self.gui.library_view.get_selected_ids())
        if not ids:
            return error_dialog(self.gui, "Nothing selected", "Select one or more books first.", show=True)

        db = self.gui.current_db.new_api
        books = []
        for book_id in ids:
            fmts = {}
            for f in db.formats(book_id):
                p = db.format_abspath(book_id, f)
                if p:
                    fmts[f.upper()] = p
            if not fmts:
                continue
            authors = db.field_for("authors", book_id) or ()
            books.append({
                "id": book_id,
                "title": db.field_for("title", book_id) or "Untitled",
                "author": " & ".join(authors),
                "formats": fmts,
            })
        if not books:
            return error_dialog(self.gui, "No formats", "None of the selected books have any file formats.", show=True)

        job = ThreadedJob(
            "rsvpnano_send",
            "Sending %d book(s) to RSVP Nano" % len(books),
            send_job,
            (PREFS["host"], category, PREFS["send_mode"], books),
            {},
            Dispatcher(self.done),
        )
        self.gui.job_manager.run_threaded_job(job)
        self.gui.status_bar.show_message("Sending to RSVP Nano...", 3000)

    def done(self, job):
        if job.failed:
            return self.gui.job_exception(job, dialog_title="Send to RSVP Nano failed")
        res = job.result
        ok = [r for r in res["results"] if r[1]]
        bad = [r for r in res["results"] if not r[1]]
        lines = []
        if ok:
            lines.append("Sent %d:" % len(ok))
            lines += ["  - %s -> %s" % (t, d) for t, _, d in ok]
        if bad:
            lines.append("")
            lines.append("Failed %d:" % len(bad))
            lines += ["  - %s: %s" % (t, d) for t, _, d in bad]
        text = "\n".join(lines) or "Nothing was sent."
        if bad:
            error_dialog(self.gui, "Send to RSVP Nano", "Some books could not be sent.", det_msg=text, show=True)
        else:
            info_dialog(self.gui, "Send to RSVP Nano", "All books sent.", det_msg=text, show=True)

