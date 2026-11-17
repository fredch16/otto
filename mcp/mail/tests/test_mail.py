"""Offline tests against a fake IMAP server (no real mail involved)."""
import email
import re
from datetime import date
from email.message import EmailMessage
from email.policy import default as policy

import httpx
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from otto_mail_mcp import server
from otto_mail_mcp.mailbox import Mailbox, html_to_text, search_criteria


def make_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF with Helvetica text; newlines start new lines."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for text in pages:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines = " T* ".join(f"({line}) Tj" for line in escaped.split("\n"))
        stream = f"BT /F1 11 Tf 14 TL 60 760 Td {lines} ET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {len(objects)} 0 R >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("cp1252")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


POLICY = make_pdf(["Versicherungsschein Nr. MAW-1\nMonatsbeitrag 34,50 EUR", "Kuendigung mit einer Frist von 3 Monaten"])
SCAN = make_pdf([""])


def message(uid_date: str, sender: str, subject: str, body: str, html: str = "", attachment: bytes = b"",
            files: list[tuple[str, str, bytes]] = ()) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Date"] = sender, "fred@icloud.com", subject, uid_date
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    if attachment:
        msg.add_attachment(attachment, maintype="application", subtype="pdf", filename="Mandat.pdf")
    for filename, content_type, content in files:
        maintype, subtype = content_type.split("/")
        if maintype == "text":
            msg.add_attachment(content.decode(), subtype=subtype, filename=filename)
        else:
            msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


class FakeIMAP:
    def __init__(self):
        self.folders = {
            "INBOX": {
                11: message("Mon, 02 Nov 2026 09:00:00 +0100", "DKB <info@dkb.de>", "Ihr Lastschriftmandat",
                            "Mandatsreferenz M-4711, Gläubiger-ID DE98ZZZ09999999999", attachment=b"%PDF-1.4"),
                12: message("Tue, 10 Nov 2026 12:00:00 +0100", "Gym <hello@fitclub.de>", "Newsletter",
                            "plain fallback", html="<p>Hallo&nbsp;Fred</p><script>x()</script><p>Kündigung bis 31.01.</p>"),
            },
            "Insurance": {
                21: message("Mon, 07 Apr 2025 10:00:00 +0200", "MAWISTA <info@mawista.com>", "Antragsbestätigung",
                            "Ihre Unterlagen anbei.", files=[
                                ("Police.pdf", "application/pdf", POLICY),
                                ("Scan.pdf", "application/pdf", SCAN),
                                ("Hinweis.html", "text/html", b"<p>Bitte&nbsp;pr\xc3\xbcfen</p>"),
                                ("Kalender.ics", "application/octet-stream", b"BEGIN:VCALENDAR"),
                                ("Police.pdf", "application/pdf", make_pdf(["Zweite Police"])),
                            ]),
            },
            "Sent Messages": {
                5: message("Wed, 04 Nov 2026 08:00:00 +0100", "fred@icloud.com", "Re: Lastschrift", "Danke, DKB."),
            },
            "Junk": {1: message("Thu, 05 Nov 2026 08:00:00 +0100", "spam@x.test", "DKB win", "DKB")},
            "Drafts": {},
        }
        self.selected = None
        self.calls = []

    def login(self, user, password):
        self.calls.append(("login", user))

    def logout(self):
        pass

    def list(self):
        return "OK", [f'(\\HasNoChildren) "/" "{name}"'.encode() for name in self.folders]

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        self.selected = folder.strip('"')
        return ("OK", [b"1"]) if self.selected in self.folders else ("NO", [b"no such folder"])

    def uid(self, command, *args):
        box = self.folders[self.selected]
        if command == "SORT":
            words = [a.decode().strip('"').lower() for a in args[2:] if a not in (b"TEXT", b"ALL")]
            hits = [uid for uid, raw in box.items()
                    if all(w in email.message_from_bytes(raw, policy=policy).as_string().lower() for w in words)]
            return "OK", [" ".join(map(str, sorted(hits, reverse=True))).encode()]
        if command == "FETCH":
            self.calls.append(("fetch", args[1]))
            data = []
            for uid in map(int, args[0].split(",")):
                raw = box[uid]
                if "HEADER.FIELDS" in args[1]:
                    raw = raw.split(b"\n\n", 1)[0] + b"\n\n"
                data += [(f"1 (BODY[] {{{len(raw)}}}".encode(), raw), f" UID {uid})".encode()]
            return "OK", data
        raise AssertionError(command)

    def append(self, folder, flags, when, raw):
        self.calls.append(("append", folder, flags))
        self.folders[folder.strip('"')][99] = raw
        return "OK", [b"APPEND completed"]


@pytest.fixture
def fake():
    imap = FakeIMAP()
    server.use(Mailbox("fred@icloud.com", "app-password", connect=lambda: imap))
    yield imap
    server.use(None)


async def test_tools_and_annotations():
    async with Client(server.mcp) as client:
        found = {t.name: t.annotations for t in (await client.list_tools()).tools}
    assert set(found) == {"search_mail", "read_mail", "list_mail_folders", "read_attachment", "file_attachment",
                          "create_draft"}
    assert all(found[name].read_only_hint
               for name in ("search_mail", "read_mail", "list_mail_folders", "read_attachment"))
    assert all(found[name].read_only_hint is False and found[name].destructive_hint is False
               for name in ("create_draft", "file_attachment"))


def test_search_is_newest_first_across_folders_and_skips_junk(fake):
    result = server.search_mail("DKB")
    assert [(r["folder"], r["uid"]) for r in result["results"]] == [("Sent Messages", 5), ("INBOX", 11)]
    assert all(readonly for call, *rest in fake.calls if call == "select" for readonly in rest[1:])
    assert all("PEEK" in call[1] for call in fake.calls if call[0] == "fetch")


def test_search_criteria():
    assert search_criteria() == [b"ALL"]
    assert search_criteria("Kündigung DKB", sender="dkb.de", since=date(2026, 11, 1)) == [
        b"TEXT", '"Kündigung"'.encode(), b"TEXT", b'"DKB"', b"FROM", b'"dkb.de"', b"SINCE", b"01-Nov-2026"]
    assert search_criteria('say "hi"') == [b"TEXT", b'"say"', b"TEXT", b'"\\"hi\\""']


def test_read_prefers_plain_text_and_lists_attachments(fake):
    mail = server.read_mail("INBOX", 11)
    assert "Mandatsreferenz M-4711" in mail["text"]
    assert mail["attachments"] == [{"filename": "Mandat.pdf", "type": "application/pdf", "bytes": 8}]
    assert all(readonly for call, *rest in fake.calls if call == "select" for readonly in rest[1:])


def test_html_only_mail_is_turned_into_text():
    assert html_to_text("<p>Hallo&nbsp;Fred</p><script>x()</script><p>Kündigung bis 31.01.</p>") == \
        "Hallo Fred\n\nKündigung bis 31.01."


def test_draft_goes_to_drafts_with_paperless_attachment(fake, monkeypatch):
    monkeypatch.setenv("PAPERLESS_URL", "http://paperless.test")
    monkeypatch.setenv("PAPERLESS_TOKEN", "token")

    def fake_get(url, **kwargs):
        assert url.endswith("/api/documents/12/download/") and kwargs["params"] == {"original": "true"}
        return httpx.Response(200, content=b"%PDF-1.4 original", request=httpx.Request("GET", url), headers={
            "content-type": "application/pdf", "content-disposition": "attachment; filename*=utf-8''Widerruf%20Mandat.pdf"})

    monkeypatch.setattr(server.httpx, "get", fake_get)
    added = []
    monkeypatch.setenv("OTTO_DATABASE_URL", "postgresql://otto.test/otto")
    monkeypatch.setattr(server.pending, "add", lambda url, *args, **kwargs: added.append(args) or 7)
    result = server.create_draft(["service@gym.example"], "Widerruf SEPA-Lastschriftmandat", "Sehr geehrte Damen und Herren,",
                                 attach_documents=[12], purpose="Cancels the gym direct debit.")
    assert result["saved_to"] == "Drafts" and result["attachments"] == ["Widerruf Mandat.pdf"]
    assert result["pending"].startswith("On Fred's Pending list as #7")
    [(kind, title, purpose, detail)] = added
    assert (kind, title, purpose) == ("draft", "Review and send: Widerruf SEPA-Lastschriftmandat",
                                      "Cancels the gym direct debit.")
    assert detail["attachments"] == [{"document_id": 12, "filename": "Widerruf Mandat.pdf"}]
    assert detail["message_id"].startswith("<") and detail["to"] == ["service@gym.example"]
    assert ("append", '"Drafts"', r"(\Draft \Seen)") in fake.calls
    draft = email.message_from_bytes(fake.folders["Drafts"][99], policy=policy)
    assert draft["From"] == "fred@icloud.com" and draft["To"] == "service@gym.example"
    assert [p.get_filename() for p in draft.iter_attachments()] == ["Widerruf Mandat.pdf"]


def test_read_attachment_gives_pdf_text_by_page(fake):
    result = server.read_attachment("Insurance", 21, "Police.pdf")
    assert result["type"] == "application/pdf" and result["bytes"] == len(POLICY)
    assert "[Page 1]\nVersicherungsschein Nr. MAW-1\nMonatsbeitrag 34,50 EUR" in result["text"]
    assert "[Page 2]\nKuendigung mit einer Frist von 3 Monaten" in result["text"]
    assert all(readonly for call, *rest in fake.calls if call == "select" for readonly in rest[1:])
    assert all("PEEK" in call[1] for call in fake.calls if call[0] == "fetch")
    assert "Zweite Police" in server.read_attachment("Insurance", 21, "Police.pdf", occurrence=2)["text"]


def test_read_attachment_html_scans_and_other_types(fake):
    assert server.read_attachment("Insurance", 21, "Hinweis.html")["text"] == "Bitte prüfen"
    assert "no text layer" in (scan := server.read_attachment("Insurance", 21, "Scan.pdf")["text"])
    assert "file_attachment" in scan
    with pytest.raises(ToolError, match="only PDFs and text"):
        server.read_attachment("Insurance", 21, "Kalender.ics")


def test_long_pdfs_are_truncated_and_read_on_from_a_page(fake, monkeypatch):
    assert server.read_attachment("Insurance", 21, "Police.pdf", from_page=2)["text"] == \
        "[Page 2]\nKuendigung mit einer Frist von 3 Monaten"
    with pytest.raises(ToolError, match="only 2 page"):
        server.read_attachment("Insurance", 21, "Police.pdf", from_page=3)
    monkeypatch.setattr(server.attachments, "text_of", lambda *args: "[Page 1]\n" + "x" * 30_000 + "\n[Page 2]\n" + "y" * 30_000)
    text = server.read_attachment("Insurance", 21, "Police.pdf")["text"]
    assert len(text) < 40_100 and text.endswith("[Truncated at 40,000 characters. Continue with from_page=2.]")


def test_missing_attachment_names_the_ones_there(fake):
    with pytest.raises(ToolError, match=r"no attachment named 'Vertrag.pdf' \(attachments: 'Police.pdf', 'Scan.pdf'"):
        server.read_attachment("Insurance", 21, "Vertrag.pdf")
    with pytest.raises(ToolError, match="2 attachment"):
        server.read_attachment("Insurance", 21, "Police.pdf", occurrence=3)


def test_file_attachment_goes_to_consume_without_overwriting(fake, monkeypatch, tmp_path):
    consume = tmp_path / "consume"
    consume.mkdir()
    monkeypatch.setenv("OTTO_CONSUME_DIR", str(consume))
    first = server.file_attachment("Insurance", 21, "Police.pdf")
    second = server.file_attachment("Insurance", 21, "Police.pdf")
    assert (first["filed_as"], second["filed_as"]) == ("Police.pdf", "Police (2).pdf")
    assert (consume / "Police.pdf").read_bytes() == (consume / "Police (2).pdf").read_bytes() == POLICY
    assert "Documents page" in first["note"]
    assert not list(tmp_path.glob(".upload-*"))  # the staged copy was moved in
    with pytest.raises(ToolError, match="not a PDF or image"):
        server.file_attachment("Insurance", 21, "Hinweis.html")
    assert sorted(p.name for p in consume.iterdir()) == ["Police (2).pdf", "Police.pdf"]


def test_nothing_can_send_mail():
    import inspect
    from otto_mail_mcp import attachments, mailbox

    source = inspect.getsource(mailbox) + inspect.getsource(server) + inspect.getsource(attachments)
    assert not re.search(r"smtplib|SMTP\(|sendmail|send_message", source)
