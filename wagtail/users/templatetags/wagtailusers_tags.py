import itertools
from collections import defaultdict

from django import template
from django.contrib.auth import get_permission_codename
from django.contrib.auth.models import Permission
from django.utils.text import camel_case_to_spaces

from wagtail import hooks
from wagtail.users.permission_order import CONTENT_TYPE_ORDER

register = template.Library()


def normalize_permission_label(permission: Permission):
    """
    Strip model name from the end of the label, e.g. "Can deliver pizza" for a
    Pizza model becomes "Can deliver". For permissions in the model's
    Meta.default_permissions with default labels, also replace underscores
    with spaces.

    This is used to display custom model permissions in the admin.

    See https://github.com/wagtail/wagtail/issues/10982.
    """
    label = permission.name
    content_type = permission.content_type
    model = content_type.model_class()
    verbose_name = default_verbose_name = content_type.name

    if model:
        default_verbose_name = camel_case_to_spaces(model._meta.object_name)

        # If it's in default_permissions and the label matches Django's default
        # label, remove the model name from the end of the label and replace
        # underscores with spaces.
        for action in model._meta.default_permissions:
            default_codename = get_permission_codename(action, model._meta)
            is_default = permission.codename == default_codename
            if is_default and permission.name.startswith(f"Can {action}"):
                return f"Can {action.replace('_', ' ')}"

    # For all other cases (including custom permissions), try to remove the
    # verbose name from the end of the label. This only works if the label
    # matches the current verbose name or Django's default verbose name.
    for name in (default_verbose_name, verbose_name):
        if label.lower().endswith(name.lower()):
            return label[: -len(name)].strip()

    return label


@register.inclusion_tag("wagtailusers/groups/includes/formatted_permissions.html")
def format_permissions(permission_bound_field):
    """
    Given a bound field with a queryset of Permission objects - which must be using
    the CheckboxSelectMultiple widget - construct a list of dictionaries for 'objects':

    'objects': [
        {
            'object': name_of_some_content_object,
            'add': checkbox,
            'change': checkbox,
            'delete': checkbox,
            'publish': checkbox,  # only if the model extends DraftStateMixin
            'custom': list_of_checkboxes_for_custom_permissions
        },
    ]

    and a list of other permissions:

    'others': [
        (any_non_add_change_delete_permission, checkbox),
    ]

    (where 'checkbox' is an object with a tag() method that renders the checkbox as HTML;
    this is a BoundWidget on Django >=1.11)

    - and returns a table template formatted with this list.

    """
    permissions = permission_bound_field.field._queryset
    # get a distinct and ordered list of the content types that these permissions relate to.
    # relies on Permission model default ordering, dict.fromkeys() retaining that order
    # from the queryset, and the stability of sorted().
    content_type_ids = sorted(
        dict.fromkeys(permissions.values_list("content_type_id", flat=True)),
        key=lambda ct: CONTENT_TYPE_ORDER.get(ct, float("inf")),
    )

    # iterate over permission_bound_field to build a lookup of individual renderable
    # checkbox objects
    # checkbox.data['value'] gives a ModelChoiceIteratorValue
    checkboxes_by_id = {
        int(checkbox.data["value"].value): checkbox
        for checkbox in permission_bound_field
    }

    object_perms = []
    other_perms = []

    # Permissions that are known by Wagtail, to be shown under their own columns.
    # Other permissions will be shown under the "custom permissions" column.
    main_permission_names = ["add", "change", "delete", "publish", "lock", "unlock"]

    # Only show the columns for these permissions if any of the model has them.
    extra_perms_exist = {
        "publish": False,
        "lock": False,
        "unlock": False,
        "custom": False,
    }
    # Batch the permission query for all content types, then group by content type
    # (instead of querying permissions for each content type separately)
    content_perms_by_ct_id = defaultdict(list)
    permissions = permissions.filter(content_type_id__in=content_type_ids)
    for permission in permissions:
        content_perms_by_ct_id[permission.content_type_id].append(permission)

    # Iterate using the sorted content_type_ids
    for ct_id in content_type_ids:
        content_perms = content_perms_by_ct_id[ct_id]
        content_perms_dict = {}
        custom_perms = []

        if content_perms[0].content_type.name == "admin":
            perm = content_perms[0]
            other_perms.append((perm, checkboxes_by_id[perm.id]))
            continue

        for perm in content_perms:
            content_perms_dict["object"] = perm.content_type.name
            checkbox = checkboxes_by_id[perm.id]
            # identify the main categories of permission, and assign to
            # the relevant dict key, else bung in the 'custom_perms' list
            permission_action = perm.codename.split("_")[0]
            if permission_action in main_permission_names:
                if permission_action in extra_perms_exist:
                    extra_perms_exist[permission_action] = True
                content_perms_dict[permission_action] = {
                    "perm": perm,
                    "checkbox": checkbox,
                }
            else:
                extra_perms_exist["custom"] = True
                perm_name = normalize_permission_label(perm)

                custom_perms.append(
                    {
                        "perm": perm,
                        "name": perm_name,
                        "selected": checkbox.data["selected"],
                    }
                )

        content_perms_dict["custom"] = custom_perms
        object_perms.append(content_perms_dict)
    return {
        "object_perms": object_perms,
        "other_perms": other_perms,
        "extra_perms_exist": extra_perms_exist,
    }


@register.inclusion_tag("wagtailadmin/shared/buttons.html", takes_context=True)
def user_listing_buttons(context, user):
    button_hooks = hooks.get_hooks("register_user_listing_buttons")
    buttons = sorted(
        itertools.chain.from_iterable(hook(context, user) for hook in button_hooks)
    )
    return {"user": user, "buttons": buttons}
