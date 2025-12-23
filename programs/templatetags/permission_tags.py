from django import template
from programs.permission_views import can_user_read, can_user_write, get_user_role

register = template.Library()

@register.simple_tag
def can_read(user, section):
    return can_user_read(user, section)

@register.simple_tag
def can_write(user, section, obj=None):
    return can_user_write(user, section, obj)

@register.simple_tag
def user_role(user):
    return get_user_role(user)
