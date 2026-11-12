"""Create or refresh OTTO's Paperless users.

Run inside the Paperless container through `scripts/setup-agent.sh`; it is
idempotent.

- `otto-agent` (read-only): view permissions on everything. Used by the MCP
  servers that models talk to.
- `otto-editor`: may also change documents and add correspondents and document
  types, but not delete anything. Used only by `otto-tidy`, which logs every
  change so it can be undone; its token never reaches an MCP server.

Both get access to every existing document, and a workflow grants it for newly
added documents.
"""
from django.contrib.auth.models import Permission, User
from guardian.shortcuts import assign_perm
from rest_framework.authtoken.models import Token

from documents.models import Document, Workflow, WorkflowAction, WorkflowTrigger

WORKFLOW = "OTTO agent: grant read access"
VIEW_PERMISSIONS = [
    "view_document",
    "view_note",
    "view_tag",
    "view_correspondent",
    "view_documenttype",
    "view_storagepath",
    "view_customfield",
]
EDIT_PERMISSIONS = ["change_document", "add_correspondent", "add_documenttype"]
USERS = {
    "otto-agent": ("AGENT", VIEW_PERMISSIONS, ["view_document"]),
    "otto-editor": ("EDITOR", VIEW_PERMISSIONS + EDIT_PERMISSIONS, ["view_document", "change_document"]),
}

workflow, created = Workflow.objects.get_or_create(name=WORKFLOW)
if created:
    trigger = WorkflowTrigger.objects.create(
        type=WorkflowTrigger.WorkflowTriggerType.DOCUMENT_ADDED,
    )
    workflow.triggers.add(trigger)
    workflow.actions.add(WorkflowAction.objects.create(type=WorkflowAction.WorkflowActionType.ASSIGNMENT))
workflow.enabled = True
workflow.save()
action = workflow.actions.first()

for username, (label, permissions, document_permissions) in USERS.items():
    user, _ = User.objects.get_or_create(username=username)
    user.is_staff = user.is_superuser = False
    user.set_unusable_password()  # API token only; no web-UI login
    user.save()
    user.user_permissions.set(Permission.objects.filter(codename__in=permissions))

    granted = 0
    for document in Document.objects.exclude(owner=None).exclude(owner=user):
        missing = [p for p in document_permissions if not user.has_perm(f"documents.{p}", document)]
        for permission in missing:
            assign_perm(permission, user, document)
        granted += bool(missing)

    action.assign_view_users.add(user)
    if "change_document" in document_permissions:
        action.assign_change_users.add(user)

    token, _ = Token.objects.get_or_create(user=user)
    print(f"OTTO_{label}_GRANTED={granted}")
    print(f"OTTO_{label}_TOKEN={token.key}")
