# GoS Admin Portal

A Django-based administrative portal for managing programs, students, parents, and mentors for GoS.

## Portal Goals
1. Reduce the administrative burden for GoS by providing a centralized platform for everything.
2. Increase transparency and accountability for parents and students of the programs.
3. Increase the data reliability of our participant data.

## Prerequisites
- Python 3.11+ (recommended)
- pip
- (Optional) virtualenv or venv

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

5. Create a superuser (to access the admin and log in during local dev)
   python manage.py createsuperuser

6. Seed the database with some sample data to play with (optional)
   python manage.py seed_db

7. Run the development server
   python manage.py runserver

8. Open the app
   Visit http://127.0.0.1:8000/ to view the portal. Log in using your superuser credentials.

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

Presently it's deployed on Render using a web service and their PostgreSQL database.