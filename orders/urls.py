from django.urls import path

from orders import views

app_name = "orders"

urlpatterns = [
    path("vendors/", views.VendorListView.as_view(), name="vendor_list"),
    path("vendors/create/", views.VendorCreateView.as_view(), name="vendor_create"),
    path(
        "vendors/<int:pk>/edit/",
        views.VendorUpdateView.as_view(),
        name="vendor_edit",
    ),
    path(
        "vendors/<int:pk>/delete/",
        views.VendorDeleteView.as_view(),
        name="vendor_delete",
    ),
    path(
        "carriers/",
        views.ShippingCarrierListView.as_view(),
        name="carrier_list",
    ),
    path(
        "carriers/create/",
        views.ShippingCarrierCreateView.as_view(),
        name="carrier_create",
    ),
    path(
        "carriers/<int:pk>/edit/",
        views.ShippingCarrierUpdateView.as_view(),
        name="carrier_edit",
    ),
    path(
        "carriers/<int:pk>/delete/",
        views.ShippingCarrierDeleteView.as_view(),
        name="carrier_delete",
    ),
    path("", views.OrderListView.as_view(), name="order_list"),
    path("archive/", views.OrderArchiveView.as_view(), name="order_archive"),
    path("items/create/", views.ItemCreateView.as_view(), name="item_create"),
    path(
        "items/<int:pk>/edit/",
        views.ItemUpdateView.as_view(),
        name="item_edit",
    ),
    path(
        "items/<int:pk>/delete/",
        views.ItemDeleteView.as_view(),
        name="item_delete",
    ),
    path("create/", views.OrderCreateView.as_view(), name="order_create"),
    path("<int:pk>/", views.OrderDetailView.as_view(), name="order_detail"),
    path("<int:pk>/edit/", views.OrderUpdateView.as_view(), name="order_edit"),
    path("<int:pk>/delete/", views.OrderDeleteView.as_view(), name="order_delete"),
    path(
        "<int:pk>/mark-ordered/",
        views.OrderMarkOrderedView.as_view(),
        name="order_mark_ordered",
    ),
    path(
        "<int:pk>/mark-shipped/",
        views.OrderMarkShippedView.as_view(),
        name="order_mark_shipped",
    ),
    path(
        "<int:pk>/mark-received/",
        views.OrderMarkReceivedView.as_view(),
        name="order_mark_received",
    ),
    path(
        "<int:pk>/mark-pending/",
        views.OrderMarkPendingView.as_view(),
        name="order_mark_pending",
    ),
    path(
        "<int:pk>/shipping/",
        views.OrderShippingUpdateView.as_view(),
        name="order_update_shipping",
    ),
    path(
        "<int:pk>/add-items/",
        views.OrderAddItemsView.as_view(),
        name="order_add_items",
    ),
    path(
        "<int:pk>/remove-item/<int:item_id>/",
        views.OrderRemoveItemView.as_view(),
        name="order_remove_item",
    ),
]
