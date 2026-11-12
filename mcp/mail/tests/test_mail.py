"""Offline tests against a fake IMAP server (no real mail involved)."""
import email
import re
from datetime import date
from email.message import EmailMessage
from email.policy import default as policy

import httpx
import pytest
from mcp import Client

from otto_mail_mcp import server
from otto_mail_mcp.mailbox import Mailbox, html_to_text, search_criteria


def message(uid_date: str, sender: str, subject: str, body: str, html: str = "", attachment: bytes = b"") -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Date"] = sender, "fred@icloud.com", subject, uid_date
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    if attachment:
        msg.add_attachment(attachment, maintype="application", subtype="pdf", filename="Mandat.pdf")
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
    assert set(found) == {"search_mail", "read_mail", "list_mail_folders", "create_draft"}
    assert all(found[name].read_only_hint for name in ("search_mail", "read_mail", "list_mail_folders"))
    assert found["create_draft"].destructive_hint is False


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
    result = server.create_draft(["service@gym.example"], "Widerruf SEPA-Lastschriftmandat", "Sehr geehrte Damen und Herren,",
                                 attach_documents=[12])
    assert result["saved_to"] == "Drafts" and result["attachments"] == ["Widerruf Mandat.pdf"]
    assert ("append", '"Drafts"', r"(\Draft \Seen)") in fake.calls
    draft = email.message_from_bytes(fake.folders["Drafts"][99], policy=policy)
    assert draft["From"] == "fred@icloud.com" and draft["To"] == "service@gym.example"
    assert [p.get_filename() for p in draft.iter_attachments()] == ["Widerruf Mandat.pdf"]


def test_nothing_can_send_mail():
    import inspect
    from otto_mail_mcp import mailbox

    source = inspect.getsource(mailbox) + inspect.getsource(server)
    assert not re.search(r"smtplib|SMTP\(|sendmail|send_message", source)
