from django.urls import path
from . import views
app_name="badges"
urlpatterns=[
 path("", views.BadgeListView.as_view(), name="list"),
 path("<int:pk>/", views.BadgeDetailView.as_view(), name="detail"),
 path("create/", views.BadgeCreateView.as_view(), name="create"),
 path("<int:pk>/edit/", views.BadgeUpdateView.as_view(), name="edit"),
 path("<int:pk>/delete/", views.BadgeDeleteView.as_view(), name="delete"),
 path("<int:pk>/award/", views.BadgeAwardView.as_view(), name="award"),
 path("<int:pk>/revoke/", views.BadgeRevokeView.as_view(), name="revoke"),
]
