import logging

from rest_framework import permissions

from core.models import PractitionerOrganization

logger = logging.getLogger(__name__)


class IsSelfUrlPath(permissions.BasePermission):

    def has_permission(self, request, view):
        return int(request.parser_context['kwargs']['pk']) == request.user.id


class IsOrganizationManager(permissions.IsAuthenticated):
    """
    Only allows organization managers to add and delete the practitioners.
    """

    def has_permission(self, request, view):
        """
        Return True if the user is an organization manager or False if not.
        Also checks the authenticated user.
        """

        if super().has_permission(request, view):
            if org_id := view.kwargs.get('pk'):
                return PractitionerOrganization.objects.filter(
                    practitioner__jhe_user=request.user,
                    organization_id=org_id,
                    role=PractitionerOrganization.ROLE_MANAGER
                ).exists()
        return False


# RBAC
ROLE_PERMISSIONS = {
    "super_user": [
        "data_source.manage",
        "organization.manage_for_practitioners",
        "patient.manage_for_organization",
        "study.manage_for_organization"],
    "manager": [
        "organization.manage_for_practitioners",
        "patient.manage_for_organization",
        "study.manage_for_organization"
    ],
    "member": [
        "patient.manage_for_organization",
        "study.manage_for_organization"
    ],
    "viewer": []
}


def IfUserCan(resource_and_action: str):
    resource, action = resource_and_action.split(".", 1)

    class _IfUserCan(permissions.IsAuthenticated):

        @staticmethod
        def if_role_can(role: str, permission: str):
            return permission in ROLE_PERMISSIONS.get(role, [])

        @staticmethod
        def get_role(view, request, resource):
            organization_id = None
            if request.user.is_superuser and resource in ["data_source", "organization", "practitioner", "patient"]:
                return "super_user"

            if view.action == 'create':
                if resource == 'patient':
                    organization_id = request.data.get('organization_id')
                elif resource == 'study':
                    organization_id = request.data.get('organization')
                elif resource == 'organization':
                    # sub organization creation
                    organization_id = request.data.get("part_of")

            else:
                # case of delete, update, partial_update
                if resource == 'patient':
                    organization_id = request.query_params.get('organization_id')
                elif resource == 'study':
                    model_obj = view.model_class.objects.filter(id=view.kwargs.get("pk")).first()
                    organization_id = model_obj.organization.id if model_obj else None
                elif resource == 'organization':
                    # get organization id or the parent organization id if nested
                    model_obj = view.model_class.objects.filter(id=view.kwargs.get("pk")).first()
                    organization_id = model_obj.part_of.id if (
                            model_obj.part_of and model_obj.part_of.id != 0) else view.kwargs.get("pk")

            link = PractitionerOrganization.objects.filter(
                practitioner__jhe_user=request.user,
                organization_id=organization_id
            ).first()
            return link.role if link else None

        def has_permission(self, request, view):

            # User has to be authenticated
            if super().has_permission(request, view):
                role = self.get_role(view, request, resource)
                return self.if_role_can(role, f"{resource}.{action}")
            return False

    return _IfUserCan
