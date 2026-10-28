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


def _export_items_csv(items):
    import csv

    from django.http import HttpResponse

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="order_requests.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
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
    )
    for item in items.iterator(chunk_size=500):
        row = _item_export_row(item)
        writer.writerow(
            [
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
        )
    return response


def _export_items_xlsx(items):
    from io import BytesIO

    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Order Requests"
    ws.append(
        [
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
    )

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(
        start_color="333333", end_color="333333", fill_type="solid"
    )
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill

    for item in items.iterator(chunk_size=500):
        row = _item_export_row(item)
        ws.append(
            [
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
        )

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = col[0].column_letter
        ws.column_dimensions[col_letter].width = max(max_len + 2, 12)

    for row in ws.iter_rows(min_row=2, max_col=17):
        for cell in row:
            if cell.column in (4, 5) and cell.value not in ("", None):
                cell.number_format = "$#,##0.00"

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="order_requests.xlsx"'
    return response


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
