# Changelog

All notable changes to this project will be documented in this file.

## 2026-08-07

### Added
- **Rate Limiting on the Public Application Wizard**: The online application is now throttled to prevent abuse. Each IP address can submit at most 10 forms per minute, each email address can request at most 5 verification codes per hour, and each application can be verified at most 10 times per hour. When a limit is hit, you'll see a friendly "Just a moment…" page and are asked to wait before trying again.

### Changed
- **Quieter Unit Test Output**: Audit log entries no longer clutter the output when running unit tests. They still appear on the console during normal use of the site.
- **Application Review List Shows Names**: The application review page now lists the student's or mentor's name instead of the applicant's email, so reviewers can identify applications at a glance. Converted mentor applications also show their status as "Converted to Mentor" instead of "Converted to Student".
- **"Active Manifest" Renamed to "Who's Here Now"**: The attendance page that shows people currently signed in is now called "Who's Here Now" in the navigation and page title. The kiosk button that opens the same list is now labeled "Who's Here Now" too.
- **Health Checks Ping Email Less Often**: The `/health` endpoint now only tests the outgoing email server once per minute (configurable via `HEALTH_SMTP_CHECK_INTERVAL`) instead of on every single probe, so Render's 5-second health checks don't hammer the SMTP server. Failed checks use a shorter cooldown so recovery is still noticed quickly.

### Fixed
- **Mentors Shown Correctly on Active Manifest**: Mentors signed in via the kiosk no longer appear as "Visitor" on the Active Manifest page. They now show their name with a Mentor badge, matching the All Program Entries page.
- **Health Checks Actually Run Now**: The `/health` endpoint (used by Render health checks) no longer redirects anonymous requests to the login page. A probe hitting `/health` without a trailing slash was previously redirected to login, and since the probe followed the redirect and saw the login page's HTTP 200, the service looked "healthy" without the database or email checks ever running. Both `/health` and `/health/` now run the real check for anyone.

## 2026-08-06

### Changed
- **Past Programs Grouped by School Year**: On the Programs landing page, past programs are now grouped under the school year (July–June) in which they ended, newest first, so it's easier to find programs from a particular season.
- **Restructured unit tests**: Regrouped and separated unit tests from dozens of files to more logical and reasonable groups.
- **Contact Info Box Removed from Dashboard**: The dashboard no longer shows the "Your Contact Information" card (name, phone, and email) that appeared after logging in. Contact details are still available on the My Profile page.
- **Mentors Can Manage Attendance**: Mentors can now close stale attendance sessions from the Active Manifest page (individually or all at once) and use the RFID Management page to view, assign, and replace RFID cards. Deleting/deactivating a card is still restricted to Lead Mentors.

### Fixed
- **Mentor Emails No Longer Say "Student"**: The approval, finalization, decline, and lead-mentor notification emails now use mentor-appropriate wording for mentor applications instead of showing an empty "Student" line. Mentors are addressed by their own name, and the approval email no longer tells them to download, sign, and re-upload documents (they're converted directly to mentors instead).
- **Kiosk Enter Key Now Signs In**: On the attendance kiosk, pressing Enter after typing in the name fields now reliably signs the person in or out, and pressing Enter after typing a visitor's team number now signs the visitor in as well.

## 2026-08-05

### Added
- **/health Endpoint for Render Health Checks**: A new `/health` endpoint verifies both the database connection and the outgoing email backend are alive. It returns `{"status": "ok", "db": "ok", "email": "ok"}` (HTTP 200) when healthy, or `{"status": "unhealthy", ...}` (HTTP 503) with per-component details on failure. This allows Render's infrastructure health checks to verify that the service, database, and email (required for OTP login) are all up without requiring authentication.

## 2026-08-04

### Fixed
- **Clearer Email Verification Code Messages**: If the verification code you type doesn't match, the application page now says it didn't match instead of warning that it "expired". A code is now reported as expired only when it has actually been open too long, and separate messages cover when there's no active code or too many wrong attempts.
- **Verification Codes No Longer Randomly Replaced**: The verification code is now emailed as soon as you enter your email address, and reloading or revisiting the verification page will not silently generate and send a new code. Previously a page refresh could issue a fresh code and invalidate the one in your inbox.

### Changed
- **Kiosk Name Lookup Finds Preferred Names**: The attendance kiosk now also searches by a person's preferred name when matching typed names, not just their legal/first and last name. So a member who goes by a nickname can type the name they actually use and still be found.
- **Kiosk Success Message Moved to Bottom**: On the kiosk sign-in screen, the green success message now appears at the bottom of the screen instead of the top, so it no longer covers the header and title.

### Added
- **Kiosk Reminder for Students**: The kiosk's Visitor/Guest tab now reminds anyone who is a Girls of Steel student to use the "Member" tab, so student hours get logged correctly. After a visitor checks in, the kiosk automatically jumps back to the Member tab, ready for the next student.

## 2026-08-03

### Added
- **Inactive Students Kept Out of Student Selection Lists**: Students who have graduated (or whose enrollment in a program was marked inactive) no longer appear in the dropdowns and selection lists used to pick a student — such as adding an existing student to a program, recording a payment, assigning a fee, creating a sliding scale discount, sending a balance email, or linking students to an adult. Previously these lists could mix inactive students in with the current roster.
- **Inactive Students Shown Separately on Program Pages**: On a program's team/crew assignment page and its photo grid, students who are no longer active are now listed in their own "Students No Longer Active" section (the same way the program detail page already shows them), so they're clearly separated from current students instead of mixed in.
- **Program Emergency Contacts Page**: A new per-program "Emergency Contacts" page lists every active student in the program along with their Primary Guardian, Secondary Guardian, and any other contacts on file. Each person's phone number and email are shown as clickable buttons, so staff can call or email them with one click. Mentors and Lead Mentors can view it; parents and students cannot.

### Changed
- **"Parents" Nav Link Becomes "Emergency Contacts"**: The program navigation bar link that previously pointed to the Parents section of a program's page now opens the new per-program Emergency Contacts page instead, under the name "Emergency Contacts". It is shown to Mentors and Lead Mentors only.
- **Sliding Scale Applies to Programs Active in the Same Time Frame**: A student's approved sliding scale discount now applies to a program only if the program's start/end dates overlap the sliding scale's effective date range. So a discount set for the current season won't discount fees from a past program that has already ended. The program's active/inactive toggle is ignored for this purpose, so a program that becomes active again automatically picks up the discount for any overlapping dates.
- **Sliding Scale Management Moved to Applications Page**: The "Add Sliding Scale" button no longer appears on a program's page. Instead, the Sliding Scale Applications page has its own "Add Sliding Scale" button, and existing sliding scales (both pending and already decided) can be edited from there. This keeps all sliding scale management in one place.
- **Sliding Scale Entry Matches the Application Form**: The "Add Sliding Scale" form now looks and works like the page parents use to apply. The student is selected first, then household size and adjusted gross income are entered at the top, with a live estimated discount that updates as you type. The discount percent, effective date, and expiration date sit below in their own section.
- **File encryption secure key update**: Convert file encryption from using SECRET_KEY to using FILE_ENCRYPTION_KEY, and ensure some checks before code run in production.

## 2026-08-02

### Fixed
- **Mentor Applications Keep Mentor Flow**: Fixed a bug where a prospective mentor who went back a few steps (e.g., to the "Who are you?" page) and continued again would be bounced into the student program-selection step instead of back through the mentor questions. Mentors now stay in the mentor wizard, and resuming/continuing an unfinished mentor application routes to the correct mentor step.
- **Dynamic Permission Logic**: Fixed a bug in `DynamicPermissionMixin` where `CreateView` operations would incorrectly attempt to fetch a non-existent object (via `get_object()`), causing a 404 or permission failure when trying to add new records (such as Fees or Payments) if a primary key was present in the URL (e.g., the parent Program's ID).
- **Mentor Application Submission wording**: The post-submission confirmation page and the submission-confirmation email previously always referred to a "student" and "primary adult contact" even for mentor applicants (who have neither). Mentors now see thank-you text and an email that correctly identify them as the applicant/mentor instead of showing student/parent information.

### Added
- **More Helpful Application Welcome Page**: The first page of the application wizard now explains who can apply (students, parents/guardians, or mentors/volunteers) and lists the programs currently accepting applications — including grade range and dates at a glance, with an expandable "More details" section on every program. You can browse what's available without starting an application.
- **Story Integration Tests**: Introduced a new testing pattern for full user lifecycles. The first suite (`programs/tests/test_integration_flows.py`) covers the complete financial journey: adding a fee, applying for a sliding scale discount, approving it, recording payments, and verifying the final balance sheet accuracy.
- **Application Flow Integration Tests**: Added a comprehensive integration test suite for the public application wizard (`applications/tests/test_integration_flows.py`). It covers both Student-initiated (including parent handoff) and Parent-initiated application lifecycles from start through lead mentor approval and conversion to student records.
- **Mentor / Volunteer Applications Re-enabled**: The "Mentor / Volunteer" option is available again in the public application wizard. Prospective mentors can now submit an application, and lead mentors can approve and convert it into a mentor record on file.
- **Improved Documentation for Reliability**: Updated `README.md` and `AGENTS.md` to explicitly require Test-Driven Development (TDD) for all new features and bug fixes, and provided guidance on when to use unit vs. integration tests.
- **CI/CD Documentation**: Added a "Continuous Integration (CI)" section to `README.md` detailing the automated checks (linting, security, system checks, and tests) that must pass before deployment, and how to execute them using the `run_ci` scripts.

## 2026-08-01

### Fixed
- **Sliding Scale Approval Error on Locked Files**: Fixed an error that could prevent a Lead Mentor from approving or declining a sliding scale application if one of the uploaded documents was still open/locked elsewhere (for example, right after being previewed or downloaded). The application is now approved/declined successfully and the document record is still cleaned up, even if the underlying file can't be removed immediately.

### Added
- **Withdraw a Sliding Scale Application**: Parents can now withdraw their own pending sliding scale application from the Payments page (for example, to fix a mistake and reapply). Withdrawing permanently removes the application and any uploaded documents, and a new application can be submitted right away.
- **Estimated Discount on the Sliding Scale Application**: While filling out the "Apply for Sliding Scale" form, parents now see a live, estimated discount percentage update automatically as they type their household size and income. This is calculated right in the browser (nothing is submitted until they click Submit), and is clearly labeled as an estimate since the final discount is still determined by a Lead Mentor's review.
- **In-Portal Tax Document Viewer**: On the Sliding Scale Review page, Lead Mentors can now click "View" to preview an uploaded tax document (PDF, image, etc.) right in the page, or "Download" to save a readable copy — both are automatically decrypted on the fly, so the raw encrypted file is never shown or downloaded directly.

### Changed
- **Clearer Payments Page Layout**: On the Payments page, the sliding scale status for each student has been moved out of the balance table and into its own "Sliding Scale" card that appears after the Total Owed, so the two sections are no longer crowded together.
- **Easier-to-Read Sliding Scale Application Form**: The "Apply for Sliding Scale" page now has clearer spacing and labeling between fields, instead of everything running together. The note about document handling was also reworded in plain language (documents are kept private and secure, and are permanently deleted after review) instead of the more technical "encrypted in transit and at rest" phrasing.

## 2026-07-31

### Added
- **Sliding Scale Applications**: Parents can now apply for the sliding scale discount directly from the Payments page. Each student's card shows whether they're currently on the sliding scale (and through what date), have an application pending review, or aren't enrolled in it yet, with an "Apply for Sliding Scale" button when appropriate. Applicants answer a short questionnaire (household size and adjusted gross income) and can optionally upload supporting documents, which are encrypted both in transit and at rest.
- **Sliding Scale Review Queue**: Lead Mentors now have a dedicated "Sliding Scale Applications" page (linked from the Admin menu and Portal Settings) to review pending applications, see the automatically suggested discount percent, and approve (setting the discount, start date, and an optional expiration date) or decline (with a required reason that's shared with the family) each one.
- **Configurable Income Guidelines**: The federal poverty guideline numbers used to calculate the suggested sliding scale discount (base amount, per-additional-household-member amount, and the lower/upper income boundary multipliers) are now editable by Lead Mentors from a new "Sliding Scale" tab in Portal Settings, instead of being hard-coded.
- **Sliding Scale Email Notifications**: Parents (who opt in to updates) now receive an email when they submit a sliding scale application and another when it's approved or declined; Lead Mentors receive an email whenever a new application comes in for review.

### Changed
- **Sliding Scale Now Applies Across All Programs**: The sliding scale discount is no longer tied to a single program. Once approved for a student, it automatically applies to that student's fees in every program they're enrolled in during the approved date range, rather than needing to be set up separately per program.
- Uploaded sliding scale documents are now automatically and permanently deleted as soon as a Lead Mentor approves or declines the application, since they're no longer needed for review.

## 2026-07-30

### Fixed
- **CI Parallel Test Crash**: Resolved a `TypeError: cannot pickle 'traceback' object` that occurred in GitHub Actions when tests failed in parallel mode.
- **Application List Sorting Test**: Fixed `test_application_list_sorting_by_email` to correctly handle the new grouped application review layout.
- **Program Date Range Display & Test Stability**: Reverted `Program.__str__` to a year-only format to maintain compatibility with existing tests, while introducing a new `name_with_dates` property for use in administrative dropdowns.
- **Attendance Test Reliability**: Fixed a 15-minute discrepancy failure in `AttendanceServiceTests.test_record_tap_explicit_in_out` by using fixed timestamps instead of `timezone.now()`, preventing failures caused by tests running across local midnight boundaries.
- **Coverage Configuration**: Added a missing `.coveragerc` file with `parallel = True` and `concurrency = multiprocessing` to ensure stable coverage reporting when running tests in parallel.

### Added
- **Test Coverage Reporting**: Integrated code test coverage into the GitHub Actions CI workflow. The CI now automatically runs tests with `coverage`, combines results from parallel test runners, and reports the final coverage percentage in the workflow logs.
- **Local Coverage Tools**: Updated local development scripts (`run_ci.ps1`, `run_ci.sh`, `run_ci.bat`) to include coverage reporting, allowing developers to check their test coverage locally with a single command.
- **Coverage Configuration**: Added a `.coveragerc` configuration file to ensure accurate coverage reporting by omitting migrations, tests, and standard Django boilerplate files.
- **Attendance "All Program Entries" management**: Added a new administrative page that lists all attendance sessions across all programs. This page is restricted to Lead Mentors and allows for inline editing and deletion of any attendance record (students, mentors, and visitors), providing a master view of all facility entries. Includes sortable headers, the ability to update programs and visitor team numbers, and 12-hour time formatting for better readability.
- **Attendance "Stale Session" management**: The Active Manifest page now automatically identifies "stale" sessions—records from previous days that were never closed. Admins can now close all stale sessions at once with a single click, or close them individually.
- **Flexible and realistic stale session durations**: When closing a stale attendance session, the system now defaults to a 1-hour duration (instead of running until the current moment). Admins can also specify a custom duration when closing all stale sessions in bulk, providing better accuracy for different daily schedules.
- **Automatic stale session cleanup**: The attendance sign-in service now automatically detects and closes a person's stale sessions from previous days when they tap in for a new session. This keeps the manifest clean and ensures forgotten sessions are recorded with realistic 1-hour durations without manual intervention.

### Changed
- **Grouped Application Review**: Reorganized the application review list into actionable categories: Admin actions (Review to convert, Review to approve) and Applicant actions (Waiting on forms, Parent data, Student data, or Drafts). This makes it easier for Lead Mentors to see what needs immediate attention.
- **Improved Application Filtering**: Replaced the "Applicant Type" filter with a more useful "Applicant Program" filter on the review screen, allowing admins to quickly focus on applications for a specific program.
- Fixed a bug on the application review screen where filtering by program would break the site's navigation menu.
- **Improved Auto-Sign-Out logic**: The attendance kiosk now only considers sign-in events from the current day when attempting to automatically close a session. If a student has an old session open from a previous day, tapping the kiosk will now start a fresh session for today instead of incorrectly closing the multi-day stale record (which is now handled by the new automatic cleanup logic).
- Improved the Program Settings and Imports attendance CSV import dropdown so programs now show their schedule in the option label (for example: Program Name (2026-01-10 - 2026-04-20)), making it easier to pick the correct program when names are similar.
- Synchronized portal import behavior with current data models: student imports now map legacy Active values to Graduated, parent and mentor imports now always set the correct role flags, and relationship imports now create links even when the relationship label is blank.
- Updated the Students sample CSV template to use Graduated (instead of Active) and aligned duplicate sample files to prevent format drift.
- Expanded attendance CSV imports to support an optional visitor team number column, so visitor attendance records can include team numbers from bulk imports.

## 2026-07-29

### Added
- Added a new parent-only **Payments** page in the main navigation that shows each student first, the programs under that student, and a total owed at the bottom, with direct **View Balance** links from that page.
- Added a **How to Pay** modal (including check instructions and a direct link to the online payment portal).

### Changed
- Reused and centralized student-program balance calculations so parent dashboards, parent payments pages, and dues reports all use the same underlying balance logic.
- Restricted the new parent Payments pages to Parents only, while keeping Students, Mentors, and Lead Mentors blocked from those parent-specific screens.
- Modify the seed_db command to reflect the latest changes to the data structures.
- Add links to Parent entries from the Student entries.
- Add an easier to manage Student-Parent relationship editor on both the Student and Parent side to ease editing and accidental breakage.

## 2026-07-28

### Changed
- **Unified Phone Numbers**: Simplified contact information for both Adults (Parents, Mentors, Alumni) and Students by replacing multiple phone fields with a single phone number, a phone type (home, cell, work, other), and an optional SMS text message consent. Existing data was automatically migrated, and the application wizard now collects this unified information.
- **Kiosk UI Enlargement**: Significantly increased the size of text, inputs, buttons, and badges on the attendance kiosk to ensure it is easily readable and usable on large touchscreens.
- **Improved Kiosk Notifications**: Moved the attendance feedback notifications (Welcome/Goodbye messages) from the bottom-right corner to a prominent top-center position. The notification text and boxes have also been enlarged for better visibility across the room.
- **Kiosk Member Sign-In robustness**: Improved the Member Sign-In tab to strictly require a registered member. If a name or RFID card is not recognized, the kiosk now provides a clear error message instead of incorrectly recording the tap as a visitor. This ensures that attendance data for students and mentors remains accurate and prevents accidental guest records.
- **Personalized Kiosk Sign-Out message**: The kiosk goodbye message is now personalized by role. Students still see their session and weekly hours for motivation. Mentors and other adults now see a simple, appreciative "Thank you for volunteering today!" message without hour stats, as their contribution is valued beyond time tracking.

### Added
- **Kiosk "Who's here?" Manifest**: Added a new "Who's here?" button to the kiosk screen that displays a live manifest of everyone currently signed in. This allows students and mentors to quickly see who is in the building via a modal without leaving the main sign-in page.
- **Dashboard attendance & outreach hours**: Student and Parent dashboards now show the number of attendance hours logged this week and total hours logged since the program started. If a program does not have attendance or outreach tracking enabled, a clear "No hours tracked" message is displayed instead of a placeholder.

## 2026-07-27

### Changed
- **Attendance permission rules tightened**: The portal now enforces clear role-based rules for who can view, add, edit, or delete attendance records. Students can only see their own attendance. Parents can only see their children's attendance. Mentors can view and add or edit student attendance records for current programs, but cannot delete any attendance records. Users with no role cannot access attendance data. Lead Mentors continue to have full access.
- **Kiosk security improvement — no API key required**: Kiosk sign-in pages no longer embed an API key in the browser. Instead, a mentor visits the kiosk URL once and enters their portal credentials to "unlock" the kiosk. After unlocking, the kiosk runs without any login for 7 days. All attendance recording and student lookups now happen through secure server-side calls — nothing sensitive is ever sent to the browser. Staff can Activate or Deactivate individual kiosks from the Settings → Kiosk Sign-In tab.
- **Visitor sign-in now tracks team number**: The kiosk Visitor / Guest tab now includes an optional "Team Number" field so visiting FRC/FTC/FLL teams can log their team number when signing in. The team number is stored alongside the visit record for reporting.
- **API Key creation simplified**: When creating a new API key, you no longer need to enter or generate a key manually. Just give it a name, pick a scope (read or read/write), and save — a secure key is generated automatically. The key is displayed read-only on the edit page so you can copy it at any time.

### Added
- **Kiosk Sign-In**: Added a new public kiosk attendance page at `/kiosk/<id>/` that students and visitors can use to tap in and out without logging in to the portal. The page is designed for a PC or tablet running in kiosk mode and supports:
    - **Member sign-in**: Students tap their RFID card (or type their full name) to automatically log an IN or OUT event.
    - **Visitor / Guest sign-in**: Non-members (other teams, visitors) can sign their name in a separate tab to log their visit.
    - A loading overlay and Bootstrap toast notifications (green for welcome, red for errors) provide instant visual feedback.
- **Kiosk Configuration**: Staff can create and manage kiosk pages from the portal settings screen (Settings → Kiosk Sign-In tab). Each configuration links a program and an API key so attendance is recorded in the right place.
- **Student Lookup API**: Added a new `GET /api/v1/attendance/student/lookup` endpoint that allows kiosks (and other integrations) to search for students by RFID card UID or by name. This endpoint is authenticated with an API key.
- **API middleware fix**: The `/api/v1/` path is now correctly exempt from the portal's login-required middleware, allowing external tools and kiosks to reach API endpoints using their API key without being redirected to the login page.
- **RFID Management Page**: Added a new administrative page at `/attendance/rfid/` that allows staff to easily associate RFID tags with students and mentors. Staff can search for a person by name, see their currently active RFID card (if any), and assign a new UID by scanning or typing.

## 2026-07-26

### Fixed
- Resolved a permissions gap where Lead Mentors added to the main system group could not access the application review pages, because two separate "Lead Mentor" groups existed in the system. These have been merged into one unified group so a Lead Mentor only needs to be added once to gain access everywhere.
- Fixed a silent bug where user accounts were not automatically added to the correct role groups (Mentor, Parent, Student) when their profile was saved. Group assignments now work correctly on profile creation and update.
- Fixed the attendance import page to use the same role-based permission system as the rest of the site, so Lead Mentors and Mentors can import attendance while Students and Parents are correctly blocked.
- Simplified and corrected the attendance viewing check so a Parent can only view their own children's attendance records, removing a duplicate and inconsistent permission check.
- Fixed a security gap where an unknown web address could show a plain "page not found" message to a signed-out visitor instead of asking them to log in first.
- Restricted Mentors so they can only view Parent contacts who currently have a student enrolled in an active program, instead of being able to see every adult record in the system.

### Added
- Added comprehensive test coverage for attendance view permissions by role, login-required middleware URL exemptions, the role-permissions settings page, and multi-role edge cases in role detection.
- Added a "My Profile" page for adults (mentors, parents, alumni), allowing them to view their personal information and linked students in one place.
- Added a "My Profile" link to the user account menu for quick access to personal records.
- Implemented object-level security: users (students and adults) can now view and edit their own profiles, but are restricted from accessing or modifying profiles of others unless they have administrative permissions.

### Changed
- Improved the navigation menu to display the user's full name instead of their email address.
- Renamed the account menu link that pointed to the Dashboard from "My Profile" to "Dashboard" for better clarity.
- Updated the system to redirect users back to their dashboard with a descriptive error message when attempting to access non-existent or unauthorized student and adult records, instead of showing a generic 404 error page.
- Enabled object-level self-editing for students and adults by moving permission checks from the URL level to the view level, ensuring that users can manage their own data while remaining securely blocked from others.
- Extended the permission system to allow parents to view and edit the information of their associated children, ensuring full access for families while maintaining strict security for non-related students.
- Added a "Students" link to the navigation menu for parents and "View Profile" / "Edit Profile" buttons to the dashboard, making it easier for families to manage student records.
- Restricted direct program access to Lead Mentors and Mentors (Active programs only), while blocking Students and Parents from accessing program details directly.
- Implemented strict role-based access control for all financial data: Restricted payments, fees, and sliding scale management to Lead Mentors only.
- Enabled parents to view their own children's balance sheets and payment details, while ensuring they are blocked from accessing other students' financial records.
- Restricted aggregated financial reports ("Dues Owed" and "Email Balances") to Lead Mentors only.
- Updated the permission system to handle object-level security for Payments, Fees, and Sliding Scale records.
- Hard-coded Mentor permissions to prevent them from viewing or managing payments, attendance, and fees, ensuring they only have access to the student roster.
- Updated the main navigation and program detail templates to hide management actions and role-inappropriate links for Mentors and other non-admin users.
- Split the Portal Settings page into focused sub-sections internally, making each category of settings (role permissions, teams, crews, sub-teams) independently manageable and easier to maintain.
- Cleaned up internal code duplication in student list views by centralizing role-based filtering logic into a shared helper, ensuring consistent behavior across all student-related pages.

## 2026-07-25

### Fixed
- Fixed the Adult Add/Edit form JavaScript not working in some browsers (like Firefox) due to a missing Content Security Policy (CSP) nonce on inline scripts.
- Fixed a crash (`IntegrityError`) that occurred during student or adult login when multiple profile records shared the same email address. The system now correctly identifies and prefers the profile already linked to a user account, preventing duplicate link attempts that violated database constraints.

### Changed
- Reorganized the Adult Add/Edit form into collapsible Bootstrap accordions for better organization and clarity.
- Updated role-specific sections (Parent, Mentor, Alumni) to be completely hidden when their corresponding role is unchecked, and automatically show/expand "in the moment" when the role is selected.

## 2026-07-23

### Fixed
- Fixed a bug where adding a new parent or adult with an email address already on file would be incorrectly rejected. Two adults (such as a mother and father) can now share the same email address without any errors in the Add or Edit forms.
- Added a new "Add Adult" page (`/programs/adults/new/`) so staff can create any adult record directly without going through the role-specific Parent or Mentor routes.
- The Add/Edit Adult form now shows mentor-specific sections (Mentor Details, Clearances, Access & Platforms) and an Alumni Details section. The mentor and alumni sections are automatically shown or hidden based on the role checkboxes selected.
- Verified that converting one application does not affect other pending or incomplete applications that share the same student or parent email. Each application's data, status, and email fields remain untouched; Adult and Student records are correctly reused (not duplicated) when a second application with the same details is later converted.
- Fixed a bug where the application wizard's "primary contact" step could prefill with the wrong parent's information when two adults share the same email address. The system now correctly identifies and prefills the parent who is already on file as a primary contact, rather than picking one arbitrarily.
- Fixed a bug where two parents or guardians sharing the same email address (e.g. a mother and father) would have their records incorrectly merged during application conversion. The system now creates separate contact records for each person, matched by both name and email, so both parents are correctly linked to the student.
- Removed the restriction that prevented two adults from having the same personal email address, allowing households where multiple guardians share one email to be properly represented in the system.
- Fixed a bug where converting an application to a student record could create a duplicate student if the applicant's last name was entered in a different case (e.g., "Smith" vs. "SMITH"). The system now matches existing students by name and date of birth in a case-insensitive way before creating a new record.
- Fixed a confusing experience where saving a student's record appeared to do nothing — the page now redirects to the student's profile after saving, and shows a confirmation message so you know the save was successful.
- Fixed the "Edit Fee" and "Add Fee" pages where input fields for name, amount, and date were missing or incorrectly rendered. Also made the page look nicer.
- Fixed a broken HTML tag in the payment recording email template.
- Resolved Bandit security findings (B106) in `programs/tests/test_inactive_student.py` by adding appropriate suppression for test-only hardcoded passwords.

### Added
- Automatically send fee information emails to parents when a student is enrolled in a program that has existing fees.
- Added a notification for specific fee assignments, ensuring parents are notified when a student is assigned to a non-global fee.
- Added an optional `due_date` field to Fees to help parents track payment deadlines.
- Displayed Fee due dates on student balance sheets (web and printable versions) and in the mentor's fee management list.
- Included Fee due dates in automated email notifications when a new fee is added and in payment confirmation notifications.

### Changed
- Optimized GitHub Actions workflows (ci.yml and codeql.yml) to prevent duplicate runs when multiple events (like a push and a pull request) are triggered simultaneously for the same branch.
- Renamed the Fee's "date" field to "effective date" to improve clarity and distinguish it from due dates.
- Refactored email notification logic for fees, payments, and sliding scale discounts.
- Centralized email sending into a reusable `send_templated_notification` utility that automatically generates plain-text versions from HTML templates.
- Removed redundant hardcoded message strings from the codebase, ensuring email content is managed exclusively through templates.
- Reworded parts of the fee and payment emails to be more clear on next steps for parents.
- Updated program roster views (Signout sheet, Schools view, Map view) to only show students currently marked as active in the program.
- Updated program emails to automatically exclude students (and their parents) who are marked as inactive in that program.
- Re-added the ability for admins to mark a student as inactive within a specific program. This is useful for tracking students who have dropped out without removing their history or graduation status.
- Added a "Program Enrollments" section to the student edit page to allow managing a student's active status in specific programs.

## 2026-07-21

### Added
- Implemented automatic email notifications for applicants (students and parents) when their application is converted to a program enrollment.
- Created new email templates for conversion notifications, informing them of their enrollment and that further program information will follow soon.
- Added a "Resend Conversion Email" button to the application review screen for lead mentors to resend enrollment notifications.

## 2026-07-20

### Changed
- Darkened the Mentor dashboard card colors from "info" (light blue) to "primary" (dark blue) to improve visibility against the white background.
- Improved Mentor dashboard by replacing the generic "View Programs" button with a list of currently active programs, providing direct access to rosters and details.
- Improved Student and Parent dashboards to show a "Withdrawn" status for students who are no longer active in a program.
- Updated `DashboardView` to move withdrawn enrollments to the "Past & Upcoming Programs" section to reduce clutter while maintaining visibility.
- Improved Student and Parent dashboards by grouping inactive and upcoming programs into collapsible Bootstrap accordions, reducing clutter while keeping program history accessible.
- Updated `DashboardView` to provide pre-grouped enrollment data for optimized dashboard rendering.
- Updated the Student and Parent dashboards to conditionally show program details:
  - Active programs are now expanded to show balance info, attendance tracking, and outreach sign-ups.
  - Inactive and Upcoming programs are now collapsed, showing only their name and a status badge.
- Added a new `status` property to programs and an "Upcoming" badge for programs starting in the future.

### Fixed
- Resolved Bandit security findings and cleaned up unused `# nosec` suppressions:
  - Fixed hardcoded password in `programs/tests/test_list_sorting.py` by adding appropriate suppression for test code.
  - Resolved `mark_safe` warnings in `programs/templatetags/sorting_tags.py` for static HTML entities.
  - Refactored `programs/templatetags/form_tags.py` to use `format_html` instead of `mark_safe`, improving security and eliminating redundant suppressions.
  - Cleaned up unnecessary `# nosec` comments in `programs/tests/test_balance_sheet.py`.

### Added
- Implemented automatic email notifications for parents when important financial actions occur:
  - Notifications sent when a new fee is added to a program (respecting individual fee assignments).
  - Notifications sent when a payment is recorded, including the payment details and the student's updated remaining balance.
  - Notifications sent when a sliding scale discount is assigned to a student.
- Created a standardized, responsive HTML email template system for these notifications, ensuring a professional look with consistent branding.
- Centralized student balance calculation logic into reusable utility functions to ensure consistency across the portal and email notifications.

## 2026-07-12

### Fixed
- Fixed a bug where the "Resend Parent Handoff" email was sent to the student's email address instead of the parent's email. It now correctly uses the parent email provided during the application process (Step 7).
- Fixed GitHub Actions CI failure in `test_student_login_provisioning.py` where `contextvars.Token` was incorrectly used as a context manager. Switched to `allauth.core.context.request_context(request)`.
- Fixed GitHub Actions `safety` check failure caused by `requirements.txt` being in UTF-16 encoding (often caused by `pip freeze` on Windows). Added an automatic conversion step to the CI workflow.

### Added
- Added a "Communications" section to the application review detail page, allowing lead mentors to resend system emails:
  - Resend OTP/Verification email (for resuming applications).
  - Resend Parent Handoff email (for students handing off to parents).
  - Resend Submission Confirmation email.
  - Resend Approval and Decline emails.
- This helps resolve issues where applicants miss or lose their application wizard emails.

## 2026-07-08

### Added
- Duplicate application detection in the application wizard:
  - When an applicant verifies their email, the system now checks for existing draft applications with the same email.
  - If a duplicate is found, the user is presented with a choice to resume the previous application or start over with a fresh one.
  - Choosing to resume deletes the current temporary application and redirects the user to their previous progress.
  - Choosing to start over deletes the previous draft application(s) and continues with the current one.
  - This prevents students from inadvertently creating multiple duplicate applications.

## 2026-07-07

### Added
- Implemented custom error handling for 403 (Forbidden), 400 (Bad Request), and 500 (Internal Server Error):
  - Added dedicated error pages (`403.html`, `400.html`, `500.html`) with consistent site branding and helpful messages.
  - Configured custom handlers in `views.py` and registered them in `urls.py`.
- Implemented custom 404 error handling:
  - Users visiting non-existent pages are now redirected to the home page with a "that page doesn't exist" message.
  - Visitors to the application wizard who encounter a 404 (e.g., due to an expired session or invalid application ID) are redirected back to the main `/apply/` page with a specific "that application doesn't exist or timed out" message.
- Configured Django message tags to map to Bootstrap 5 alert classes (e.g., `error` maps to `danger`), ensuring error messages appear in the appropriate red "error boxes."

### Changed
- Updated `LoginRequiredMiddleware` to allow 404 errors to pass through to the custom handler even for unauthenticated users, ensuring consistent redirection behavior across the site.

## 2026-07-05

### Fixed
- Fixed a bug where Students and Parents could not log in via the OTP email code, even if their email was on file. The login system now correctly creates an account for them on first login, instead of saying "no account found."
- Resolved "disconnected student info" issue:
  - Implemented automatic name synchronization between `Student`/`Adult` profiles and their linked `User` accounts. Profiles are now the authoritative source for names.
  - Protected the `user` field in student edit forms to prevent accidental disconnection or unauthorized changes by non-admins.
- Fixed a crash in Django Admin when editing Student profiles (KeyError 'user').

## 2026-07-04

### Fixed
- Resolved login issues for Students and Mentors converted from applications. Verified emails are now correctly saved to Student/Adult records even if the form fields were left blank in the application wizard.
- Fixed a bug where mentor applications were incorrectly processed as student applications during conversion. Mentors now correctly result in an `Adult` record with the mentor flag set.
- Fixed several field name and name handling bugs in the application conversion service (`preferred_name` instead of `preferred_first_name`, handling of `legal_first_name` and `andrew_id` for mentors).
- Relaxed the mentor login policy to allow any email ending in `@andrew.cmu.edu` if it belongs to the mentor's record, supporting tagged email addresses (e.g., `name+tag@andrew.cmu.edu`) for testing and flexibility.
- Fixed a bug in `AccountAdapter.send_mail` where the `PRINT_LOGIN_CODE_ALWAYS` environment variable was incorrectly interpreted.
- Resolved Django Admin error when editing a Student: removed a stale `active` field reference from `StudentAdmin`.

### Added
- New env var `PRINT_LOGIN_CODE_ALWAYS` to aid debugging OTP logins. When set (e.g., `1`/`true`), the adapter logs an INFO line with the login code (or `(none)`) and email for all login email attempts, including the `unknown_account` path. Existing behavior for `DEBUG`/staging remains unchanged.

### Changed
- Login policy updated to enable anyone with a modeled role to sign in via OTP with role-specific identifiers:
  - Students: may sign in with their Andrew email or personal email.
  - Parents: may sign in with their personal email only (Andrew email not accepted).
  - Mentors (including Lead Mentors): may sign in with their Andrew email only (personal email not accepted).
  - Alumni: may sign in with their personal email only (Andrew email not accepted).
- The authentication adapter now enforces these rules and still auto-provisions a `User` account and `EmailAddress` when a matching Student/Adult exists but no user has been linked yet.

## 2026-07-02

### Added
- New application open and close dates for Programs, allowing applications to remain open after a program's start date.

### Changed
- Updated the application wizard to look at explicit application dates for program availability.
- Application open and close dates now default to the program's start and end dates.
- Application wizard Step 4 UI: Program blurbs are now inside collapsible accordions so applicants can quickly scan programs and expand for more details.

## 2026-07-01

### Changed
- Unified the layout and styling of login and account management pages (Login, Sign In, Verify Identity, and Sign Out).
- Updated all authentication pages to use a consistent "Girls of Steel Portal" branding and wider card layout (720px).
- Added error message displays and support contact information to all authentication screens for better user assistance.

## 2026-06-29

### Fixed
- Fixed bug on Edit Parent screen where role flags (e.g., `is_parent`) were reset to False when saving.
- Fixed field name mismatch in Parent edit form that prevented saving the email address.
- Fixed data loss on Edit Adult screen where address and Andrew ID info were reset to empty on save.
- Fixed security vulnerability where non-Lead Mentors could edit their own role flags (e.g., `is_mentor`).

### Added
- Audit logging for user login, logout, and failed login attempts.
- Audit logging for sensitive data access (Student and Adult profiles) by Mentors and Lead Mentors.
- `SensitiveDataViewMixin` for consistent logging across sensitive detail and update views.

### Changed
- Improved `ParentForm` to explicitly include only relevant fields and preserve existing role flags.
- Updated Adult edit templates to include missing fields like address, Andrew ID, and CMU access details.

## 2026-06-16

### Changed
- Security: Updated dependencies and addressed Bandit security findings.
- Improved formatting and fixed broken unit tests.

## 2026-06-15

### Added
- Student and adult login functionality, with improved email handling.

## 2026-06-10

### Changed
- UI: Replaced navbar with context-aware navbar.

## 2026-06-08

### Fixed
- Fixed migration issue with duplicate email addresses.

## 2026-05
### Added
- Added grade management for programs and grade confirmation in the application wizard.
- Implemented sliding scale information display in the application wizard.
- Added support for non-destructive data imports.
### Changed
- Security: Upgraded Django and dependencies to address vulnerabilities.
- UI improvements across the application, including help text and form formatting.
### Fixed
- Bugs in application wizard data consistency.
- Test failures after Django upgrade, and OSV scanner/scheduler issues.

## 2026-04
### Added
- Enhanced viewing options for sliding scale balance sheets.
- Enabled emailing balance sheets to individual students.
### Changed
- Updated requirements per OSV security scans.
- Improved linting, formatting, and Bandit compliance.

## 2026-03
### Added
- Additional files for team/role assignments.
### Changed
- Extensive cleanup of false-positive security scan results (Semgrep, GitLeaks).
### Fixed
- Fixed sliding scale application logic for student fees.

## 2026-01 — 2026-02
### Added
- Integrated comprehensive security scanning (CodeQL, GitLeaks, Semgrep, Trivy, OSV).
- Added team and crew management features.
- Implemented global settings page.

## 2025-12
### Added
- Implemented one-time password (OTP) authentication, replacing Google/regular auth.
- Added role permission settings and logging for sensitive operations.
### Changed
- Improved CI/CD pipeline with GitHub Actions and pip caching.

## 2025-10 — 2025-11
### Added
- Attendance tracking features and API key management.
### Changed
- Improved balance sheet email templates and student list sorting.
- Enhanced student profile management (cropping, attendance tweaks).

## 2025-09-07
- Initial project creation with basic student, program, parent, and mentor data forms
