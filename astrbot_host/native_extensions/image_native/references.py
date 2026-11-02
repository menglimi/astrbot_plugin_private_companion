"""Exact selection and role binding for invocation-local image references."""

from .types import MAX_REFERENCE_IMAGES


EDIT_REFERENCE_ROLES = frozenset({
    "identity", "outfit", "pose", "scene", "style", "background", "continuity", "edit_source",
})


class ReferenceSelectionError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def select_edit_tickets(arguments, inbound, saved_by_id):
    indices = arguments.get("reference_indices")
    if indices is None:
        selected_inbound = tuple(inbound)
    else:
        if (type(indices) is not list or len(indices) > MAX_REFERENCE_IMAGES
                or any(type(index) is not int or not 0 <= index < MAX_REFERENCE_IMAGES
                       for index in indices)
                or len(set(indices)) != len(indices)):
            raise ReferenceSelectionError("invalid_reference_indices")
        if any(index >= len(inbound) for index in indices):
            raise ReferenceSelectionError("native_reference_index_unavailable")
        selected_inbound = tuple(inbound[index] for index in indices)

    reference_ids = arguments.get("reference_ids", [])
    if (type(reference_ids) is not list or len(reference_ids) > MAX_REFERENCE_IMAGES
            or any(type(identifier) is not str or not identifier for identifier in reference_ids)
            or len(set(reference_ids)) != len(reference_ids)):
        raise ReferenceSelectionError("invalid_reference_ids")
    if any(identifier not in saved_by_id for identifier in reference_ids):
        raise ReferenceSelectionError("native_reference_id_unavailable")
    return (*selected_inbound, *(saved_by_id[identifier] for identifier in reference_ids))


def edit_role(arguments, selected, ticket):
    position = next((index for index, item in enumerate(selected) if item is ticket), None)
    if position is None:
        raise ReferenceSelectionError("reference_not_selected")
    roles = arguments.get("reference_roles")
    if roles is not None:
        if type(roles) is not list or len(roles) != len(selected):
            raise ReferenceSelectionError("native_reference_roles_mismatch")
        role = roles[position]
    elif "roles" not in ticket:
        role = "edit_source"
    elif len(ticket["roles"]) == 1:
        role = ticket["roles"][0]
        role = role.value if hasattr(role, "value") else role
    else:
        raise ReferenceSelectionError("native_reference_role_required")

    if role not in EDIT_REFERENCE_ROLES:
        raise ReferenceSelectionError("native_reference_role_unavailable")
    declared = ticket.get("roles")
    if declared is not None:
        declared = {item.value if hasattr(item, "value") else item for item in declared}
        if role not in declared:
            raise ReferenceSelectionError("native_reference_role_unavailable")
    return role
