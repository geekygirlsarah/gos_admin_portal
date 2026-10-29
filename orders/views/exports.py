from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone

from programs.permission_views import get_user_role


def _item_export_row(item):
    order = item.order
    return {
        "program": item.program.name if item.program else "",
        "item_name": item.item_name,
        "quantity": item.quantity_normalized,
        "unit_price": item.unit_price if item.unit_price is not None else "",
        "total": item.total if item.total is not None else "",
        "url": item.url,
        "order": f"#{order.pk}" if order else "",
        "vendor": order.vendor_name if order else item.vendor_name_display,
        "vendor_url": order.vendor_url if order else item.vendor_url,
        "requested_by": item.requested_by_name,
        "requested_on": (
            timezone.localtime(item.requested_at).strftime("%Y-%m-%d %H:%M")
            if item.requested_at
            else ""
        ),
        "notes": item.notes,
        "team": str(item.team) if item.team_id else "",
        "crew": item.crew.name if item.crew_id else "",
        "subteam": item.subteam.name if item.subteam_id else "",
        "status": item.get_status_display(),
        "ordered_on": (
            timezone.localtime(order.ordered_at).strftime("%Y-%m-%d %H:%M")
            if order and order.ordered_at
            else ""
        ),
    }


_ORDER_ITEMS_EXPORT_HEADERS = [
    "Program",
    "Item",
    "Quantity",
    "Unit Price",
    "Total",
    "Link",
    "Order",
    "Vendor",
    "Vendor Website",
    "Requested By",
    "Requested On",
    "Notes",
    "Team",
    "Crew",
    "Subteam",
    "Status",
    "Ordered On",
]


def _order_item_export_values(item):
    row = _item_export_row(item)
    return [
        row["program"],
        row["item_name"],
        row["quantity"],
        row["unit_price"],
        row["total"],
        row["url"],
        row["order"],
        row["vendor"],
        row["vendor_url"],
        row["requested_by"],
        row["requested_on"],
        row["notes"],
        row["team"],
        row["crew"],
        row["subteam"],
        row["status"],
        row["ordered_on"],
    ]


def _export_items_csv(items):
    from programs.utils.spreadsheets import export_csv_response

    rows = (_order_item_export_values(item) for item in items.iterator(chunk_size=500))
    return export_csv_response(
        filename="order_requests.csv",
        headers=_ORDER_ITEMS_EXPORT_HEADERS,
        rows=rows,
    )


def _export_items_xlsx(items):
    from openpyxl.styles import Font, PatternFill

    from programs.utils.spreadsheets import export_xlsx_response

    rows = (_order_item_export_values(item) for item in items.iterator(chunk_size=500))
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(
        start_color="333333", end_color="333333", fill_type="solid"
    )

    return export_xlsx_response(
        filename="order_requests.xlsx",
        headers=_ORDER_ITEMS_EXPORT_HEADERS,
        rows=rows,
        sheet_title="Order Requests",
        header_font=header_font,
        header_fill=header_fill,
        auto_fit_columns=True,
        currency_columns=(4, 5),
    )


class _OrderExportMixin:
    """Handles ``?export=csv`` / ``?export=xlsx`` downloads (Lead Mentors only).

    Views provide ``get_export_items()`` returning the item queryset to
    export (pending pool + pending orders for the list, ordered orders for the
    archive).
    """

    def _handle_export(self, items):
        export = self.request.GET.get("export")
        if not export:
            return None
        if get_user_role(self.request.user) != "LeadMentor":
            messages.error(self.request, "Only Lead Mentors can export orders.")
            return redirect("orders:order_list", program_id=self.program.id)
        if export == "csv":
            return _export_items_csv(items)
        if export == "xlsx":
            return _export_items_xlsx(items)
        return None

    def get(self, request, *args, **kwargs):
        response = self._handle_export(self.get_export_items())
        if response is not None:
            return response
        return super().get(request, *args, **kwargs)
