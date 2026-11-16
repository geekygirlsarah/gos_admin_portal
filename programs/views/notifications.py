from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import FormView, TemplateView, View

from organizations.platform_views import SuperuserRequiredMixin
from programs.models import Notification, UserNotificationStatus
from programs.notification_forms import NotificationForm
from programs.notification_rendering import (
    notification_message_for_display,
    notification_message_text,
)
from programs.notification_utils import visible_notifications_for_user
from programs.permission_views import LeadMentorRequiredMixin


class NotificationListView(LoginRequiredMixin, TemplateView):
    template_name = "programs/notification_list.html"
    platform_scope = False

    def get_notification_organization(self):
        return getattr(self.request, "organization", None)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        notifications = visible_notifications_for_user(
            self.request.user,
            self.get_notification_organization(),
        )
        for notification in notifications:
            notification.display_message = notification_message_for_display(
                notification
            )
        unread_notifications = [
            notification for notification in notifications if not notification.is_seen
        ]
        read_notifications = [
            notification for notification in notifications if notification.is_seen
        ]
        unread_count = sum(not notification.is_seen for notification in notifications)
        self.request.notification_unread_count = unread_count
        context["notifications"] = notifications
        context["unread_notifications"] = unread_notifications
        context["read_notifications"] = read_notifications
        context["unread_notification_count"] = unread_count
        context["platform_scope"] = self.platform_scope
        return context


class PlatformNotificationListView(SuperuserRequiredMixin, NotificationListView):
    platform_scope = True

    def get_notification_organization(self):
        return None


class NotificationMarkAsReadView(LoginRequiredMixin, View):
    platform_scope = False

    def get_notification_organization(self):
        return getattr(self.request, "organization", None)

    def post(self, request, pk):
        notifications = visible_notifications_for_user(
            request.user,
            self.get_notification_organization(),
        )
        notification = next(
            (item for item in notifications if item.pk == pk),
            None,
        )
        if notification is None:
            raise Http404

        status, _ = UserNotificationStatus.objects.get_or_create(
            user=request.user,
            notification=notification,
        )
        if not status.is_seen:
            status.is_seen = True
            status.save(update_fields=["is_seen", "updated_at"])
        if self.platform_scope:
            return redirect("platform_admin:notification_list")
        return redirect("notification_list")


class PlatformNotificationMarkAsReadView(
    SuperuserRequiredMixin, NotificationMarkAsReadView
):
    platform_scope = True

    def get_notification_organization(self):
        return None


class NotificationDismissView(NotificationMarkAsReadView):
    def post(self, request, pk):
        notifications = visible_notifications_for_user(
            request.user,
            self.get_notification_organization(),
        )
        notification = next(
            (item for item in notifications if item.pk == pk),
            None,
        )
        if notification is None:
            raise Http404

        status, _ = UserNotificationStatus.objects.get_or_create(
            user=request.user,
            notification=notification,
        )
        if not status.is_dismissed:
            status.is_dismissed = True
            status.save(update_fields=["is_dismissed", "updated_at"])
        if self.platform_scope:
            return redirect("platform_admin:notification_list")
        return redirect("notification_list")


class PlatformNotificationDismissView(SuperuserRequiredMixin, NotificationDismissView):
    platform_scope = True

    def get_notification_organization(self):
        return None


class NotificationUnreadCountView(LoginRequiredMixin, View):
    def get(self, request):
        notifications = visible_notifications_for_user(
            request.user,
            getattr(request, "organization", None),
        )
        unread_count = sum(not notification.is_seen for notification in notifications)
        return JsonResponse({"unread_count": unread_count})


class NotificationBadgeView(LoginRequiredMixin, View):
    def get(self, request):
        notifications = visible_notifications_for_user(
            request.user,
            getattr(request, "organization", None),
        )
        return JsonResponse(
            {
                "unread_count": sum(
                    not notification.is_seen for notification in notifications
                ),
                "notifications": [
                    {
                        "id": notification.pk,
                        "title": notification.title,
                        "message": notification_message_text(notification)[:100]
                        + (
                            "..."
                            if len(notification_message_text(notification)) > 100
                            else ""
                        ),
                        "type": notification.notification_type,
                        "is_seen": notification.is_seen,
                    }
                    for notification in notifications[:5]
                ],
            }
        )


class OrganizationLeadMentorRequiredMixin(LeadMentorRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "organization", None) is None:
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class NotificationCreateView(
    LoginRequiredMixin,
    OrganizationLeadMentorRequiredMixin,
    FormView,
):
    form_class = NotificationForm
    template_name = "programs/notification_form.html"
    success_url = reverse_lazy("notification_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["scope"] = "tenant"
        return kwargs

    def form_valid(self, form):
        form.save(
            organization=self.request.organization,
            created_by=self.request.user,
        )
        return super().form_valid(form)


class PlatformNotificationCreateView(SuperuserRequiredMixin, FormView):
    form_class = NotificationForm
    template_name = "programs/notification_form.html"
    success_url = reverse_lazy("platform_admin:notification_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["scope"] = "platform"
        return kwargs

    def form_valid(self, form):
        form.save(organization=None, created_by=self.request.user)
        return super().form_valid(form)


class NotificationManagementContextMixin:
    template_name = "programs/notification_management_list.html"
    platform_scope = False

    def get_notifications(self):
        raise NotImplementedError

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        notifications = self.get_notifications()
        for notification in notifications:
            notification.display_message = notification_message_for_display(
                notification
            )
        context["notifications"] = notifications
        context["platform_scope"] = self.platform_scope
        return context


class NotificationManagementListView(
    LoginRequiredMixin,
    OrganizationLeadMentorRequiredMixin,
    NotificationManagementContextMixin,
    TemplateView,
):
    def get_notifications(self):
        organization = self.request.organization
        return (
            Notification.objects.for_organization(organization)
            .filter(
                organization=organization,
                is_platform_wide=False,
                is_active=True,
            )
            .select_related("created_by", "organization")
        )


class PlatformNotificationManagementListView(
    SuperuserRequiredMixin,
    NotificationManagementContextMixin,
    TemplateView,
):
    platform_scope = True

    def get_notifications(self):
        return Notification.objects.filter(
            organization__isnull=True,
            is_platform_wide=True,
            is_active=True,
        ).select_related("created_by")


class NotificationWithdrawalMixin:
    platform_scope = False
    success_url_name = "notification_management"

    def get_notification_queryset(self):
        organization = self.request.organization
        return Notification.objects.for_organization(organization).filter(
            organization=organization,
            is_platform_wide=False,
            is_active=True,
        )

    def post(self, request, pk):
        notification = get_object_or_404(self.get_notification_queryset(), pk=pk)
        notification.is_active = False
        notification.save(update_fields=["is_active", "updated_at"])
        return redirect(self.success_url_name)


class NotificationWithdrawView(
    LoginRequiredMixin,
    OrganizationLeadMentorRequiredMixin,
    NotificationWithdrawalMixin,
    View,
):
    pass


class PlatformNotificationWithdrawView(
    SuperuserRequiredMixin,
    NotificationWithdrawalMixin,
    View,
):
    platform_scope = True
    success_url_name = "platform_admin:notification_management"

    def get_notification_queryset(self):
        return Notification.objects.filter(
            organization__isnull=True,
            is_platform_wide=True,
            is_active=True,
        )
