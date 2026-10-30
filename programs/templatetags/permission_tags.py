from django import template

from programs.permission_views import (
    can_user_delete,
    can_user_read,
    can_user_write,
    get_user_role,
)

register = template.Library()


@register.simple_tag(takes_context=True)
def can_read(context, user, section, obj=None, org=None):
    if org is None and context:
        request = context.get("request")
        if request is not None:
            org = getattr(request, "organization", None)
    return can_user_read(user, section, obj=obj, org=org)


@register.simple_tag(takes_context=True)
def can_write(context, user, section, obj=None, org=None):
    if org is None and context:
        request = context.get("request")
        if request is not None:
            org = getattr(request, "organization", None)
    return can_user_write(user, section, obj=obj, org=org)


@register.simple_tag(takes_context=True)
def can_delete(context, user, section, obj=None, org=None):
    if org is None and context:
        request = context.get("request")
        if request is not None:
            org = getattr(request, "organization", None)
    return can_user_delete(user, section, obj=obj, org=org)


@register.simple_tag
def user_role(user):
    return get_user_role(user)
