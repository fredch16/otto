"""Create or refresh the read-only `otto-agent` Paperless user.

Run inside the Paperless container through `scripts/setup-agent.sh`; it is
idempotent. The user gets view-only model permissions, view access to every
existing document, and a workflow grants view access to newly added documents.
"""
from django.contrib.auth.models import Permission, User
from guardian.shortcuts import assign_perm
from rest_framework.authtoken.models import Token

from documents.models import Document, Workflow, WorkflowAction, WorkflowTrigger

USERNAME = "otto-agent"
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

user, _ = User.objects.get_or_create(username=USERNAME)
user.is_staff = user.is_superuser = False
user.set_unusable_password()  # API token only; no web-UI login
user.save()
user.user_permissions.set(Permission.objects.filter(codename__in=VIEW_PERMISSIONS))

granted = 0
for document in Document.objects.exclude(owner=None).exclude(owner=user):
    if not user.has_perm("documents.view_document", document):
        assign_perm("view_document", user, document)
        granted += 1

workflow, created = Workflow.objects.get_or_create(name=WORKFLOW)
if created:
    trigger = WorkflowTrigger.objects.create(
        type=WorkflowTrigger.WorkflowTriggerType.DOCUMENT_ADDED,
    )
    action = WorkflowAction.objects.create(
        type=WorkflowAction.WorkflowActionType.ASSIGNMENT,
    )
    action.assign_view_users.add(user)
    workflow.triggers.add(trigger)
    workflow.actions.add(action)
workflow.enabled = True
workflow.save()

token, _ = Token.objects.get_or_create(user=user)
print(f"OTTO_AGENT_GRANTED={granted}")
print(f"OTTO_AGENT_TOKEN={token.key}")
