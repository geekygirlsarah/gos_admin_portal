"""
URL configuration for GoSAdminPortal project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path, reverse_lazy
from django.views.generic import RedirectView, TemplateView
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("programs/", include("programs.urls")),
    path("api/v1/", include("api.urls")),
    path("api-keys/", include("api.manage_urls")),
    path("profile/", include("portal.urls")),
    # Public application flow (new wizard lives in the `applications` app)
    path("apply/", include("applications.urls")),
    path(
        "privacy/",
        TemplateView.as_view(template_name="privacy.html"),
        name="privacy_policy",
    ),
    path(
        "non-discrimination/",
        TemplateView.as_view(template_name="non_discrimination.html"),
        name="non_discrimination_policy",
    ),
    # Root redirects to the dashboard (home)
    path(
        "",
        RedirectView.as_view(url=reverse_lazy("profile_dashboard"), permanent=False),
        name="home",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    urlpatterns += [
        re_path(
            r"^media/(?P<path>.*)$",
            serve,
            {
                "document_root": settings.MEDIA_ROOT,
            },
        ),
    ]
