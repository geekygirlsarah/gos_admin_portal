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
   Visit http://127.0.0.1:8000/ or http://localhost:8000 to view the portal. Log in using your superuser credentials.

## Project Structure
The repository is organized into several Django apps:
- `programs/`: The core application managing programs, students, adults (parents/mentors), fees, payments, and enrollments.
- `applications/`: A multi-step public application wizard (`/apply/`) and staff review workflow.
- `attendance/`: Kiosk-based attendance tracking using RFIDs and names, including visitor management.
- `portal/`: Shared dashboard views and global settings management.
- `api/`: Versioned REST API (`/api/v1/`) for external integrations and kiosks.
- `audit/`: Audit logging for sensitive data access and authentication events.
- `GoSAdminPortal/`: Project configuration, middleware, and authentication adapters.
- `templates/`: Centralized Bootstrap 5 templates, organized by app and user role.

## Key Technologies
- **Django 5.2**: Web framework with global login enforcement.
- **django-allauth**: Handles authentication via Email OTP (no passwords required for most users).
- **Bootstrap 5**: Responsive UI and components.
- **PostgreSQL**: Production database (SQLite is used for local development).
- **Pillow & openpyxl**: Image processing and Excel import/export capabilities.

## Environment Variables
Typical settings for email, debug, and allowed hosts can be configured directly in GoSAdminPortal/settings.py for local development. For production, consider using environment variables and a .env loader.

**Required in production:**
- `FILE_ENCRYPTION_KEY` — Fernet key for encrypting medical/tax documents and sensitive fields.
  Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
  If this key is rotated, all existing encrypted data (EncryptedFileField, EncryptedTextField, EncryptedCharField) must be re-encrypted using a data migration script.
- `SECRET_KEY` — Django secret key for cryptographic signing. Must NOT use the default insecure value.
  Generate with: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`
- `ALLOWED_HOSTS` — Comma-separated list of your production domain(s), e.g., `example.com,www.example.com`
- `EMAIL_HOST_USER` — SMTP username for sending OTP login emails (required for allauth email OTP)
- `EMAIL_HOST_PASSWORD` — SMTP password for the above account

**Recommended in production:**
- `DATABASE_URL` — PostgreSQL connection string (defaults to SQLite if not set)
- `ADMIN_EMAILS` — Comma-separated admin emails for error notifications
- `DEFAULT_FROM_EMAIL` — From address for outgoing emails
- `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` — Set to "True" (default)
- `SECURE_HSTS_SECONDS` — HSTS max-age in seconds (default: 31536000)

## Additional Documentation
For detailed architectural guidance, data model summaries, and coding standards (especially for AI agents), see [AGENTS.md](AGENTS.md).

## Running Tests
Tests are located in each app's `tests/` directory. Run all tests with:
   python manage.py test --parallel

To run specific integration flows:
   python manage.py test programs.tests.test_integration_flows
   python manage.py test applications.tests.test_integration_flows

We follow a TDD approach. New features or bug fixes should include both unit tests and, where appropriate, "Story" integration tests that cover full lifecycles.

## Continuous Integration (CI)
Before deploying, all changes should pass the automated CI suite. You can run these checks locally using the provided scripts:
- **Windows (PowerShell)**: `.\run_ci.ps1`
- **Windows (Batch)**: `run_ci.bat`
- **Linux/macOS**: `./run_ci.sh`

The CI suite performs the following:
- **Linting & Formatting**: Runs `flake8` (critical errors), `black` (code style), and `isort` (import sorting) to ensure consistency.
- **Security Scans**: Uses `bandit` for security best practices, `semgrep` for static analysis, and `safety` to check for known vulnerabilities in dependencies.
- **Django System Check**: Executes `python manage.py check` to verify configuration and model integrity.
- **Automated Testing**: Runs the complete suite of unit and integration tests with `coverage` reporting.

## Deployment Notes
- Use a production-ready database (PostgreSQL, MySQL) instead of SQLite.
- Configure ALLOWED_HOSTS and DEBUG in settings.
- Serve static files with whitenoise or via your web server.

Presently it's deployed on Render using a web service and their PostgreSQL database.