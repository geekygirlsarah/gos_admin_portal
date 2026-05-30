from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from GoSAdminPortal.middleware import LoginRequiredMiddleware


class MiddlewareAsyncTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="testuser")

    def test_sync_middleware_auth(self):
        def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/programs/")
        request.user = self.user
        response = middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")

    def test_sync_middleware_anon(self):
        def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/programs/")
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_sync_middleware_exempt(self):
        def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/accounts/login/")
        request.user = AnonymousUser()
        response = middleware(request)
        self.assertEqual(response.status_code, 200)

    async def test_async_middleware_auth(self):
        async def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/programs/")
        request.user = self.user
        response = await middleware(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")

    async def test_async_middleware_anon(self):
        async def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/programs/")
        request.user = AnonymousUser()
        response = await middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    async def test_async_middleware_exempt(self):
        async def get_response(request):
            return HttpResponse("OK")

        middleware = LoginRequiredMiddleware(get_response)
        request = self.factory.get("/accounts/login/")
        request.user = AnonymousUser()
        response = await middleware(request)
        self.assertEqual(response.status_code, 200)
