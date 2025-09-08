# GoS Admin Portal

A Django-based administrative portal for managing programs, students, parents, and mentors for GoS.

## Features
- Google login via django-allauth
- Manage Programs (create/list/detail)
- Manage Students, Parents, Mentors
- Bootstrap 5 UI

## Prerequisites
- Python 3.11+ (recommended)
- pip
- (Optional) virtualenv or venv
- A Google OAuth client (for production Google login). For local development you can disable social login and use the Django admin/superuser.

## Getting Started (Local Development)

1. Clone the repo
   git clone https://github.com/your-org/GoSAdminPortal.git
   cd GoSAdminPortal

2. Create and activate a virtual environment (recommended)
   python -m venv .venv
   .venv\\Scripts\\activate

3. Install dependencies
   pip install -r requirements.txt

4. Apply database migrations
   python manage.py migrate

5. Create a superuser (to access the admin and log in without Google during local dev)
   python manage.py createsuperuser

6. Run the development server
   python manage.py runserver

7. Open the app
   Visit http://127.0.0.1:8000/ to view the portal. Log in using your superuser credentials (or set up Google OAuth as below).

## Google OAuth (django-allauth)
This project uses django-allauth for Google Sign-In.

To enable Google login locally:
- Create OAuth 2.0 credentials at https://console.cloud.google.com/apis/credentials
- Authorized JavaScript origins: http://127.0.0.1:8000
- Authorized redirect URIs: http://127.0.0.1:8000/accounts/google/login/callback/
- Add a Social App in Django admin:
  - Go to http://127.0.0.1:8000/admin/
  - Sites -> ensure example.com or your local site is configured (or add 127.0.0.1:8000)
  - Social applications -> Add a new app
    - Provider: Google
    - Name: GoS Admin Portal
    - Client id: <your_client_id>
    - Secret key: <your_client_secret>
    - Sites: select your site

If you prefer not to configure Google locally, log in with the superuser and navigate the portal.

## Project Structure
- GoSAdminPortal/ ... Django project settings and URL routing
- programs/ ... App with models, views, forms, and URLs for programs, students, parents, mentors
- templates/ ... HTML templates using Bootstrap 5
- manage.py ... Django management utility

## Environment Variables
Typical settings for email, debug, and allowed hosts can be configured directly in GoSAdminPortal/settings.py for local development. For production, consider using environment variables and a .env loader.

## Running Tests
If tests are added later, they can be executed with:
   python manage.py test

## Deployment Notes
- Use a production-ready database (PostgreSQL, MySQL) instead of SQLite.
- Configure ALLOWED_HOSTS and DEBUG in settings.
- Serve static files with whitenoise or via your web server.

## License
Proprietary/Internal (update as appropriate).
