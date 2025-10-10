from django.urls import path
from .views import ApiKeyListView, ApiKeyCreateView, ApiKeyUpdateView, ApiKeyDeleteView

urlpatterns = [
    path('', ApiKeyListView.as_view(), name='api_key_list'),
    path('new/', ApiKeyCreateView.as_view(), name='api_key_create'),
    path('<int:pk>/edit/', ApiKeyUpdateView.as_view(), name='api_key_edit'),
    path('<int:pk>/delete/', ApiKeyDeleteView.as_view(), name='api_key_delete'),
]
