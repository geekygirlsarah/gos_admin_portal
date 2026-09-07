from django import forms

from orders.models import PurchaseOrder, Vendor

# Sentinel value for the "Not listed" option in the order form's vendor
# dropdown. Chosen with a value unlikely to collide with a Vendor primary key.
NOT_LISTED_VENDOR = "__not_listed__"


class OrderForm(forms.ModelForm):
    vendor_choice = forms.ChoiceField(
        required=False,
        label="Vendor",
        help_text=(
            "Pick a vendor from our list, or choose 'Not listed' and type " "your own."
        ),
    )
    vendor_name = forms.CharField(
        required=False,
        max_length=255,
        label="Vendor name",
        widget=forms.TextInput(attrs={"placeholder": "e.g. McMaster-Carr"}),
        help_text="Required when you pick 'Not listed'.",
    )
    vendor_url = forms.URLField(
        required=False,
        max_length=500,
        label="Vendor website",
        widget=forms.URLInput(attrs={"placeholder": "https://…"}),
    )

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
                    "placeholder": "Size, color, exact part number, etc.",
                }
            ),
        }
        labels = {
            "item_name": "Item",
            "unit_price": "Unit price ($)",
            "url": "Link to item",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(vendor.pk, vendor.name) for vendor in Vendor.objects.all()]
        choices.append((NOT_LISTED_VENDOR, "Not listed — I'll specify below"))
        self.fields["vendor_choice"].choices = [
            ("", "Select a vendor (optional)")
        ] + choices

        instance = getattr(self, "instance", None)
        if instance and instance.pk:
            if instance.vendor_id:
                self.fields["vendor_choice"].initial = str(instance.vendor_id)
            elif instance.vendor_name:
                self.fields["vendor_choice"].initial = NOT_LISTED_VENDOR
            self.fields["vendor_name"].initial = instance.vendor_name
            self.fields["vendor_url"].initial = instance.vendor_url

    def clean(self):
        cleaned = super().clean()
        choice = cleaned.get("vendor_choice")
        if choice == NOT_LISTED_VENDOR and not cleaned.get("vendor_name"):
            self.add_error(
                "vendor_name", "Enter a vendor name when choosing 'Not listed'."
            )
        return cleaned

    def save(self, commit=True):
        order = super().save(commit=False)
        choice = self.cleaned_data.get("vendor_choice")
        if choice and choice != NOT_LISTED_VENDOR:
            vendor = Vendor.objects.filter(pk=choice).first()
            order.vendor = vendor
            order.vendor_name = vendor.name if vendor else ""
            order.vendor_url = vendor.website if vendor else ""
        elif choice == NOT_LISTED_VENDOR:
            order.vendor = None
            order.vendor_name = self.cleaned_data.get("vendor_name", "")
            order.vendor_url = self.cleaned_data.get("vendor_url", "")
        else:
            # Blank selection: reset any previously chosen vendor so an edit
            # that intentionally deselects it clears the snapshot too.
            order.vendor = None
            order.vendor_name = ""
            order.vendor_url = ""
        if commit:
            order.save()
            self.save_m2m()
        return order


class VendorForm(forms.ModelForm):
    class Meta:
        model = Vendor
        fields = ["name", "website", "contact_email", "contact_phone", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. McMaster-Carr"}),
            "website": forms.URLInput(attrs={"placeholder": "https://…"}),
            "contact_email": forms.EmailInput(
                attrs={"placeholder": "orders@example.com"}
            ),
            "contact_phone": forms.TextInput(attrs={"placeholder": "(412) 555-0133"}),
            "notes": forms.Textarea(
                attrs={"rows": 3, "placeholder": "e.g. PO required over $500"}
            ),
        }
        labels = {
            "name": "Vendor name",
            "website": "Website",
        }
        help_texts = {
            "name": "Visible in the order form dropdown.",
            "website": "Where to place the order (shown on orders and exports).",
        }
