from django import template
from django.utils.safestring import mark_safe
from django.forms import widgets

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

@register.simple_tag
def render_field(field, **kwargs):
    """Render a Django form field with Bootstrap 5 markup, label, help, and errors.
    Usage: {% render_field form.my_field readonly=True %}
    """
    try:
        widget = field.field.widget
        base_classes = widget.attrs.get('class', '')
        
        # Merge kwargs into attrs, handling 'class' specially
        attrs = {**widget.attrs, **kwargs}
        if 'class' in kwargs:
            attrs['class'] = f"{base_classes} {kwargs['class']}".strip()
        
        # Determine appropriate control class
        if isinstance(widget, widgets.CheckboxInput):
            # Single checkbox
            input_html = field.as_widget(attrs={**attrs, 'class': f"form-check-input {attrs.get('class', base_classes)}".strip()})
            label_html = field.label_tag(attrs={'class': 'form-check-label'}) if field.label else ''
            content = f'<div class="form-check">{input_html}{label_html}</div>'
        elif isinstance(widget, widgets.CheckboxSelectMultiple):
            # Group of checkboxes
            label_html = field.label_tag(attrs={'class': 'form-label'}) if field.label else ''
            input_html = field.as_widget(attrs=attrs)
            content = f"{label_html}{input_html}"
        else:
            # Selects get form-select, others form-control
            if isinstance(widget, (widgets.Select, widgets.SelectMultiple)):
                ctrl_class = 'form-select'
            else:
                ctrl_class = 'form-control'
            
            final_class = f"{ctrl_class} {attrs.get('class', base_classes)}".strip()
            input_html = field.as_widget(attrs={**attrs, 'class': final_class})
            label_html = field.label_tag(attrs={'class': 'form-label'}) if field.label else ''
            content = f"{label_html}{input_html}"
            
        # Help text and errors
        help_html = f'<div class="form-text">{field.help_text}</div>' if field.help_text else ''
        errors_html = ''
        if field.errors:
            error_items = ''.join(f'<div class="invalid-feedback d-block">{e}</div>' for e in field.errors)
            errors_html = error_items
        return mark_safe(f'<div class="mb-3">{content}{help_html}{errors_html}</div>')
    except Exception:
        # Fallback: default rendering
        return mark_safe(f'<div class="mb-3">{field}</div>')

@register.simple_tag
def requires_bg(student, program):
    """Return True/False whether the student requires a background check for the program.
    Usage: {% requires_bg student program as needs_bg %}
    """
    try:
        return bool(student.requires_background_check(program))
    except Exception:
        return False
