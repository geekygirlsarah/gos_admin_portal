from django import forms

from orders.models import Order, OrderItem, ShippingCarrier, Vendor
from programs.models import Crew, Program, SubTeam, Team
from programs.utils import GroupedProgramChoiceField

# Sentinel value for the "Not listed" option in the order form's vendor
# dropdown. Chosen with a value unlikely to collide with a Vendor primary key.
NOT_LISTED_VENDOR = "__not_listed__"


class OrderItemForm(forms.ModelForm):
    """Form students/mentors use to place a requested item.

    The request can optionally tag a vendor (from the reference list or a
    free-text "Not listed" entry), mirroring the order form's vendor picker.
    """

    vendor_choice = forms.ChoiceField(
        required=False,
        label="Vendor",
        help_text="Which vendor would you like this item from? Optional tip for the mentor grouping orders.",
    )
    vendor_name = forms.CharField(
        required=False,
        max_length=255,
        label="Vendor name",
        widget=forms.TextInput(attrs={"placeholder": "e.g. McMaster-Carr"}),
        help_text="Only needed if you picked 'Not listed'.",
    )
    vendor_url = forms.URLField(
        required=False,
        max_length=500,
        label="Vendor website",
        widget=forms.URLInput(attrs={"placeholder": "https://…"}),
    )
    team = forms.ModelChoiceField(
        queryset=Team.objects.none(),
        required=False,
        label="Team",
        widget=forms.Select,
        empty_label="No team",
        help_text="Optional team this item is for.",
    )
    crew = forms.ModelChoiceField(
        queryset=Crew.objects.none(),
        required=False,
        label="Crew",
        widget=forms.Select,
        empty_label="No crew",
        help_text="Optional crew/project this item is for.",
    )
    subteam = forms.ModelChoiceField(
        queryset=SubTeam.objects.none(),
        required=False,
        label="Subteam",
        widget=forms.Select,
        empty_label="No subteam",
        help_text="Optional subteam this item is for.",
    )

    class Meta:
        model = OrderItem
        fields = [
            "item_name",
            "quantity",
            "unit_price",
            "url",
            "team",
            "crew",
            "subteam",
            "notes",
        ]
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

    def __init__(self, *args, program=None, **kwargs):
        super().__init__(*args, **kwargs)

        if program is None:
            instance = getattr(self, "instance", None)
            if instance and getattr(instance, "program_id", None):
                program = instance.program

        self.fields["team"].queryset = Team.objects.order_by("team_type", "number")
        if program:
            self.fields["crew"].queryset = Crew.objects.filter(
                program=program
            ).order_by("name")
            self.fields["subteam"].queryset = SubTeam.objects.filter(
                program=program
            ).order_by("name")
        else:
            self.fields["crew"].queryset = Crew.objects.order_by("name")
            self.fields["subteam"].queryset = SubTeam.objects.order_by("name")

        choices = [(vendor.pk, vendor.name) for vendor in Vendor.objects.all()]
        choices.append((NOT_LISTED_VENDOR, "Not listed — I'll specify below"))
        self.fields["vendor_choice"].choices = [("", "No preference")] + choices

        instance = getattr(self, "instance", None)
        if instance and instance.pk:
            if instance.vendor_id:
                self.fields["vendor_choice"].initial = str(instance.vendor_id)
            elif instance.vendor_name:
                self.fields["vendor_choice"].initial = NOT_LISTED_VENDOR
            self.fields["vendor_name"].initial = instance.vendor_name
            self.fields["vendor_url"].initial = instance.vendor_url

    def save(self, commit=True):
        item = super().save(commit=False)
        choice = self.cleaned_data.get("vendor_choice")
        if choice and choice != NOT_LISTED_VENDOR:
            vendor = Vendor.objects.filter(pk=choice).first()
            item.vendor = vendor
            item.vendor_name = vendor.name if vendor else ""
            item.vendor_url = vendor.website if vendor else ""
        elif choice == NOT_LISTED_VENDOR:
            item.vendor = None
            item.vendor_name = self.cleaned_data.get("vendor_name", "")
            item.vendor_url = self.cleaned_data.get("vendor_url", "")
        else:
            item.vendor = None
            item.vendor_name = ""
            item.vendor_url = ""
        if commit:
            item.save()
        return item


class OrderForm(forms.ModelForm):
    """Form mentors/Lead Mentors use to create a grouped order.

    The group picks the vendor and selects the requested items to include. Item
    membership is chosen at creation only; later additions/removals happen from
    the order detail page so editing never silently unassigns items.
    """

    program = GroupedProgramChoiceField(
        queryset=Program.objects.none(),
        required=False,
        label="Program",
        empty_label="General (no program)",
    )
    vendor_choice = forms.ChoiceField(
        required=True,
        label="Vendor",
        help_text=(
            "Pick a vendor from our list, or choose 'Not listed' to specify your own."
        ),
    )
    vendor_name = forms.CharField(
        required=False,
        max_length=255,
        label="Vendor name",
        widget=forms.TextInput(attrs={"placeholder": "e.g. McMaster-Carr"}),
        help_text="Only needed if you want to type a vendor that isn't in the list.",
    )
    vendor_url = forms.URLField(
        required=False,
        max_length=500,
        label="Vendor website",
        widget=forms.URLInput(attrs={"placeholder": "https://…"}),
    )
    items = forms.ModelMultipleChoiceField(
        queryset=OrderItem.objects.none(),
        required=False,
        label="Items to include",
        widget=forms.CheckboxSelectMultiple,
        help_text="Group the requested items you want placed together. Only unassigned requests are listed.",
    )

    class Meta:
        model = Order
        fields = ["program", "notes", "shipping_cost", "tax"]
        widgets = {
            "notes": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Anything the vendor should know."}
            ),
            "shipping_cost": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "tax": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["program"].queryset = Program.objects.all()
        self.fields["program"].initial = self.initial.get("program")

        choices = [(vendor.pk, vendor.name) for vendor in Vendor.objects.all()]
        choices.append((NOT_LISTED_VENDOR, "Not listed — I'll specify below"))
        self.fields["vendor_choice"].choices = [("", "Select a vendor")] + choices

        instance = getattr(self, "instance", None)
        if instance and instance.pk:
            # Item membership is managed from the order detail page.
            self.fields.pop("items")
            if instance.vendor_id:
                self.fields["vendor_choice"].initial = str(instance.vendor_id)
            elif instance.vendor_name:
                self.fields["vendor_choice"].initial = NOT_LISTED_VENDOR
            self.fields["vendor_name"].initial = instance.vendor_name
            self.fields["vendor_url"].initial = instance.vendor_url
        else:
            self.fields["items"].queryset = (
                OrderItem.objects.filter(order__isnull=True)
                .select_related("program", "vendor", "requested_by")
                .order_by("vendor_name", "vendor__name", "-requested_at")
            )

    def clean(self):
        cleaned = super().clean()
        if not self.instance.pk and not cleaned.get("items"):
            self.add_error("items", "Select at least one item for the order.")
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
            # Only reachable with data that bypasses the form's required
            # validation; reset the snapshot defensively.
            order.vendor = None
            order.vendor_name = ""
            order.vendor_url = ""
        if commit:
            order.save()
            if self.fields.get("items"):
                item_pks = self.cleaned_data.get("items", [])
                if item_pks:
                    OrderItem.objects.filter(pk__in=item_pks).update(order=order)
        return order


class ShippingInfoForm(forms.ModelForm):
    """Shipping/tracking details for a placed order.

    Filled in once an order is marked ordered/shipped so the team can follow
    packages. Wrapped in its own form so the vendor/program editing surface
    stays focused.
    """

    shipping_carrier = forms.ModelChoiceField(
        queryset=ShippingCarrier.objects.none(),
        required=False,
        label="Carrier",
        empty_label="No carrier",
        widget=forms.Select,
        help_text="Pick from the shipping companies list.",
    )

    class Meta:
        model = Order
        fields = [
            "shipping_carrier",
            "tracking_number",
            "shipped_date",
            "delivery_estimate",
        ]
        labels = {
            "tracking_number": "Tracking",
            "shipped_date": "Shipped",
            "delivery_estimate": "Est. delivery",
        }
        widgets = {
            "tracking_number": forms.TextInput(
                attrs={"placeholder": "Tracking number"}
            ),
            "shipped_date": forms.DateInput(attrs={"type": "date"}),
            "delivery_estimate": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["shipping_carrier"].queryset = ShippingCarrier.objects.order_by(
            "name"
        )


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


class ShippingCarrierForm(forms.ModelForm):
    class Meta:
        model = ShippingCarrier
        fields = ["name", "tracking_url_template"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. UPS"}),
            "tracking_url_template": forms.TextInput(
                attrs={
                    "placeholder": "https://www.ups.com/track?track=yes&trackNums={number}"
                }
            ),
        }
        labels = {
            "name": "Shipping company",
        }
        help_texts = {
            "name": "Visible in the shipping form dropdown on orders.",
            "tracking_url_template": (
                "Use {number} where the tracking number goes, so the tracking "
                "number on an order becomes a clickable link."
            ),
        }

    def clean_tracking_url_template(self):
        template = self.cleaned_data["tracking_url_template"]
        if template and "{number}" not in template:
            raise forms.ValidationError(
                "Include {number} in the URL where the tracking number should go."
            )
        return template
