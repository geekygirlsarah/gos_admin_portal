# Purchase Requests, Orders, and Vendor Management

This document details purchase requests (`OrderItem`), grouped orders (`Order`), vendor tracking, shipping carriers, and permissions.

## Two-Level Request & Order Architecture

Order management uses a two-level model hierarchy in `orders/models.py`:
1. **`OrderItem` (Item Level)**: Individual items requested by students or mentors.
2. **`Order` (Order Level)**: Grouped purchase orders placed with a specific vendor.

### Item Request Pool & Derived Status
- An `OrderItem` with `order=None` sits in the unassigned **Request Pool** (`order_list` page).
- **Status Invariant**: Status is strictly **derived, never stored** via `OrderItem.status_key`:
  - `"pending"`: Unassigned pool item.
  - `"ready"`: Assigned to a pending `Order` (ready to be placed).
  - `"ordered"`, `"shipped"`, `"received"`: Inherited from parent `Order.status`.
- Display labels are retrieved via `OrderItem.get_status_display()` (e.g., `"Pending"`, `"Ready to order"`, `"Ordered"`).
- Deleting an `Order` (`OrderDeleteView`) returns all its `OrderItem` records back to the unassigned pool rather than deleting them.

### Grouping Orders (`Order`)
- Mentors group pool items into an `Order` for a specific `Vendor` and `Program`.
- **Snapshot Fields**: `vendor_name` and `vendor_url` snapshot values from the vendor at creation time.
- **Financial Calculation**: `Order.total` dynamically sums item costs (`quantity * unit_price`) plus `shipping_cost` and `tax`. Returns `None` if items are unpriced and extras are zero.

---

## Vendors & Shipping Carriers

### Vendor Reference List (`Vendor`)
- Org-wide directory of approved suppliers (`name`, `website`, `contact_email`, `contact_phone`, `notes`).
- Managed by Lead Mentors under `/orders/vendors/`.
- Order creation requires vendor selection; a custom unlisted vendor option (`NOT_LISTED_VENDOR = "__not_listed__"`) allows ad-hoc supplier entry.

### ShippingCarrier & Tracking URLs (`ShippingCarrier`)
- Org-wide directory of carriers (`name`, `tracking_url_template`).
- `tracking_url_template` must contain `{number}` (validated by `ShippingCarrierForm`).
- `Order.tracking_url` dynamically formats the carrier template with `Order.tracking_number`.
- Deleting a carrier sets `Order.shipping_carrier = NULL` but preserves the plain-text tracking number.

---

## Order Permission Sections (`ORDERS_SECTIONS`)

Access is divided into four permission sections in `programs/models/`:
- **`orders-view`**: Gating for viewing active and archived order lists.
- **`orders-request`**: Allows placing item requests (`obj is None`) and editing one's own unassigned `OrderItem` records (`requested_by_id == user.pk and order_id is None`).
- **`orders-manage`**: Allows mentors to create and manage grouped `Order` objects and assign/remove items (students are never permitted).
- **`orders-shipping`**: Allows mentors to update carrier, tracking, and shipping costs on placed orders.
- Deletion of orders or items is restricted strictly to Lead Mentors (`can_user_delete`).

---

## Lifecycle, Archive, and Exports

### Lifecycle Transitions
- **`pending` → `ordered`**: Placed with vendor (`OrderMarkOrderedView`).
- **`ordered` → `shipped`**: Carrier tracking added (`OrderMarkShippedView`).
- **`shipped` → `received`**: Items arrived (`OrderMarkReceivedView`).

### Archive Gating
**Only `received` orders appear in `order_archive`**. Placed and shipped orders remain in the active orders view so staff can continue adding tracking and shipping details.

### Line-Item Exports (`_OrderExportMixin`)
Exports operate at the **line-item level** for financial accounting:
- Active Export: Request pool items + non-received order items.
- Archive Export: Received order items.
- Columns: Program, Item, Quantity, Unit Price, Total, URL, Order #, Vendor, Vendor Website, Requested By, Requested On, Notes, Team, Crew, Subteam, Item Status, Ordered On.
