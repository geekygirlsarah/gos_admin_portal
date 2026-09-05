from django.urls import path

from orders import views

app_name = "orders"

urlpatterns = [
    path("", views.OrderListView.as_view(), name="order_list"),
    path("archive/", views.OrderArchiveView.as_view(), name="order_archive"),
    path("create/", views.OrderCreateView.as_view(), name="order_create"),
    path("<int:pk>/edit/", views.OrderUpdateView.as_view(), name="order_edit"),
    path("<int:pk>/delete/", views.OrderDeleteView.as_view(), name="order_delete"),
    path(
        "<int:pk>/mark-ordered/",
        views.OrderMarkOrderedView.as_view(),
        name="order_mark_ordered",
    ),
    path(
        "<int:pk>/mark-pending/",
        views.OrderMarkPendingView.as_view(),
        name="order_mark_pending",
    ),
]
