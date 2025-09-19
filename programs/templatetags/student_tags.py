from django import template

register = template.Library()


@register.simple_tag
def requires_bg(student, program):
    """Return True/False whether the student requires a background check for the program.
    Falls back to False if data is insufficient.
    Usage: {% requires_bg student program as needs_bg %}
    """
    try:
        return bool(student.requires_background_check(program))
    except Exception:
        return False


@register.simple_tag
def yesno(value, yes='Yes', no='No'):
    """Return Yes/No strings for a boolean value. Avoids conflict with built-in yesno filter when used in tags.
    Usage: {% yesno some_bool %}
    """
    return yes if bool(value) else no
