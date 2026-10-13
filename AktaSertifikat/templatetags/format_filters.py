from django import template

register = template.Library()

@register.filter
def rupiah(value):
    try:
        value = float(value)
    except (ValueError, TypeError):
        return value
    return "Rp {:,.2f}".format(value).replace(",", "X").replace(".", ",").replace("X", ".")