"""IMAP access to Fred's mailbox: search, read and save drafts.

There is deliberately no SMTP code: OTTO cannot send mail. Drafts land in the
mailbox's Drafts folder, where Fred reviews and sends them from his own mail app.
Messages are fetched with BODY.PEEK, so reading never marks mail as read.
"""
from __future__ import annotations

import email
import imaplib
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.policy import default as policy
from email.utils import formatdate, make_msgid, parsedate_to_datetime
from html.parser import HTMLParser

PROVIDERS = {
    "icloud": {"host": "imap.mail.me.com", "drafts": "Drafts"},
    "gmail": {"host": "imap.gmail.com", "drafts": "[Gmail]/Drafts"},
}
# Not searched unless asked for explicitly.
SKIPPED_FOLDERS = {"Junk", "Deleted Items", "Deleted Messages", "Notes", "Drafts", "[Gmail]/Trash", "[Gmail]/Spam"}
MAX_TEXT_CHARS = 30_000


class MailError(Exception):
    """A failure the caller can explain to the model."""


@dataclass
class Summary:
    folder: str
    uid: int
    date: datetime | None
    sender: str
    to: str
    subject: str


@dataclass
class Attachment:
    filename: str
    content_type: str
    size: int


@dataclass
class Message:
    summary: Summary
    cc: str
    text: str
    attachments: list[Attachment]
    message_id: str


def quote(value: str) -> bytes:
    """An IMAP quoted string (UTF-8, for SEARCH/SORT with CHARSET UTF-8)."""
    return ('"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"').encode()


def folder_arg(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def search_criteria(query: str = "", sender: str = "", since: date | None = None,
                    before: date | None = None) -> list[bytes]:
    """Every word of the query must occur somewhere in the message (headers or body)."""
    criteria: list[bytes] = []
    for word in query.split():
        criteria += [b"TEXT", quote(word)]
    if sender:
        criteria += [b"FROM", quote(sender)]
    if since:
        criteria += [b"SINCE", since.strftime("%d-%b-%Y").encode()]
    if before:
        criteria += [b"BEFORE", before.strftime("%d-%b-%Y").encode()]
    return criteria or [b"ALL"]


class _TextFromHTML(HTMLParser):
    BLOCKS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "blockquote"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.skip:
            self.skip -= 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextFromHTML()
    parser.feed(markup)
    text = re.sub(r"[ \t ]+", " ", "".join(parser.parts))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def parse_message(folder: str, uid: int, raw: bytes) -> Message:
    msg = email.message_from_bytes(raw, policy=policy)
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        content = body.get_content()
        text = html_to_text(content) if body.get_content_type() == "text/html" else content.strip()
    attachments = [
        Attachment(part.get_filename() or "(unnamed)", part.get_content_type(),
                   len(part.get_payload(decode=True) or b""))
        for part in msg.iter_attachments()
    ]
    return Message(summarise(folder, uid, msg), str(msg.get("Cc", "")), text, attachments,
                   str(msg.get("Message-ID", "")))


def summarise(folder: str, uid: int, msg) -> Summary:
    try:
        sent = parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else None
    except (TypeError, ValueError):
        sent = None
    if sent and sent.tzinfo is None:
        sent = sent.replace(tzinfo=timezone.utc)
    return Summary(folder, uid, sent, str(msg.get("From", "")), str(msg.get("To", "")), str(msg.get("Subject", "")))


def fetched(data) -> list[tuple[int, bytes]]:
    """(uid, payload) pairs from an IMAP FETCH response."""
    results = []
    for index, item in enumerate(data):
        if isinstance(item, tuple):
            # the UID may come before the literal or after it, in the next item
            following = data[index + 1] if index + 1 < len(data) and isinstance(data[index + 1], bytes) else b""
            match = re.search(rb"UID (\d+)", item[0]) or re.search(rb"UID (\d+)", following)
            if match:
                results.append((int(match.group(1)), item[1]))
    return results


class Mailbox:
    def __init__(self, user: str, password: str, provider: str = "icloud", *, connect=None):
        if provider not in PROVIDERS:
            raise MailError(f"Unknown mail provider {provider!r} (supported: {', '.join(PROVIDERS)}).")
        self.user, self.password = user, password
        self.host, self.drafts = PROVIDERS[provider]["host"], PROVIDERS[provider]["drafts"]
        self._connect = connect or (lambda: imaplib.IMAP4_SSL(self.host, 993, timeout=30))

    def _session(self):
        try:
            imap = self._connect()
            imap.login(self.user, self.password)
        except (imaplib.IMAP4.error, OSError) as error:
            raise MailError(f"Could not log in to {self.host}: {error}") from error
        return imap

    def folders(self) -> list[str]:
        imap = self._session()
        try:
            return self._folders(imap)
        finally:
            imap.logout()

    @staticmethod
    def _folders(imap) -> list[str]:
        _, data = imap.list()
        names = []
        for line in data:
            match = re.search(rb'"[^"]*" "?((?:[^"\\]|\\.)*)"?$', line)
            if match:
                names.append(match.group(1).decode().replace('\\"', '"'))
        return names

    def search(self, criteria: list[bytes], folders: list[str] | None = None, limit: int = 20) -> list[Summary]:
        """Newest matching messages across folders."""
        imap = self._session()
        found: list[Summary] = []
        try:
            for folder in folders or [f for f in self._folders(imap) if f not in SKIPPED_FOLDERS]:
                status, _ = imap.select(folder_arg(folder), readonly=True)
                if status != "OK":
                    raise MailError(f"No folder named {folder!r}.")
                status, data = imap.uid("SORT", "(REVERSE DATE)", "UTF-8", *criteria)
                uids = data[0].split()[:limit] if status == "OK" and data[0] else []
                if not uids:
                    continue
                _, data = imap.uid("FETCH", b",".join(uids).decode(),
                                   "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
                for uid, header in fetched(data):
                    found.append(summarise(folder, uid, email.message_from_bytes(header, policy=policy)))
        except imaplib.IMAP4.error as error:
            raise MailError(f"Mail search failed: {error}") from error
        finally:
            imap.logout()
        oldest = datetime.min.replace(tzinfo=timezone.utc)
        return sorted(found, key=lambda s: s.date or oldest, reverse=True)[:limit]

    def read(self, folder: str, uid: int) -> Message:
        imap = self._session()
        try:
            status, _ = imap.select(folder_arg(folder), readonly=True)
            if status != "OK":
                raise MailError(f"No folder named {folder!r}.")
            _, data = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
        except imaplib.IMAP4.error as error:
            raise MailError(f"Could not read message {uid}: {error}") from error
        finally:
            imap.logout()
        messages = fetched(data)
        if not messages:
            raise MailError(f"No message {uid} in {folder!r}.")
        return parse_message(folder, uid, messages[0][1])

    def save_draft(self, to: list[str], subject: str, body: str, *, cc: list[str] | None = None,
                   attachments: list[tuple[str, bytes, str]] = (), in_reply_to: str = "") -> EmailMessage:
        """Store a new message in the Drafts folder. Nothing is sent."""
        msg = EmailMessage()
        msg["From"] = self.user
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.user.rsplit("@", 1)[-1])
        if in_reply_to:
            msg["In-Reply-To"] = msg["References"] = in_reply_to
        msg.set_content(body)
        for filename, content, content_type in attachments:
            maintype, _, subtype = content_type.partition("/")
            msg.add_attachment(content, maintype=maintype, subtype=subtype or "octet-stream", filename=filename)
        imap = self._session()
        try:
            status, data = imap.append(folder_arg(self.drafts), r"(\Draft \Seen)", None, msg.as_bytes())
        except imaplib.IMAP4.error as error:
            raise MailError(f"Could not save the draft: {error}") from error
        finally:
            imap.logout()
        if status != "OK":
            raise MailError(f"Could not save the draft: {data}")
        return msg
