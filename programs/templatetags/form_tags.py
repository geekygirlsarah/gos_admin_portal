from django import template

register = template.Library()

@register.filter(name='add_class')
def add_class(field, css):
    """Add a CSS class to a form field widget when rendering in a template.
    Usage: {{ form.field|add_class:"form-control" }}
    """
    try:
        return field.as_widget(attrs={**(field.field.widget.attrs or {}), 'class': f"{(field.field.widget.attrs.get('class', '') + ' ' + css).strip()}"})
    except Exception:
        # Fallback to default rendering if anything unexpected occurs
        return field
