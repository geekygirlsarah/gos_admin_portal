from django import forms

from orders.models import PurchaseOrder


class OrderForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ["item_name", "quantity", "unit_price", "url", "notes"]
        widgets = {
            "item_name": forms.TextInput(attrs={"placeholder": "e.g. 2mm hex driver"}),
            "quantity": forms.NumberInput(attrs={"min": "0.01", "step": "0.01"}),
            "unit_price": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "url": forms.URLInput(attrs={"placeholder": "https://…"}),
            "notes": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Size, color, exact part number, preferred vendor, etc.",
                }
            ),
        }
        labels = {
            "item_name": "Item",
            "unit_price": "Unit price ($)",
            "url": "Link to item",
        }
