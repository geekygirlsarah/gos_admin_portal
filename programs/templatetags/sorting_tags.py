from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def sort_url(context, field):
    request = context["request"]
    current_sort = context.get("current_sort")
    current_dir = context.get("current_dir", "asc")

    params = request.GET.copy()

    if current_sort == field:
        # Toggle direction
        new_dir = "desc" if current_dir == "asc" else "asc"
        params["dir"] = new_dir
    else:
        params["sort"] = field
        params["dir"] = "asc"

    return "?" + params.urlencode()


@register.simple_tag(takes_context=True)
def sort_icon(context, field):
    current_sort = context.get("current_sort")
    current_dir = context.get("current_dir", "asc")

    if current_sort == field:
        if current_dir == "asc":
            return mark_safe("&nbsp;&uarr;")
        else:
            return mark_safe("&nbsp;&darr;")
    return ""
