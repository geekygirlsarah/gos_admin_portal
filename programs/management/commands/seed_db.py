from django.core.management.base import BaseCommand
from programs.models import Program, Student, Enrollment, Adult, ProgramFeature, School
from django.utils import timezone
from datetime import date, timedelta

class Command(BaseCommand):
    help = 'Seeds the database with test data for development'

    def handle(self, *args, **options):
        self.stdout.write('Seeding database...')

        # 0. Schools
        high_school, _ = School.objects.get_or_create(name="Pittsburgh High", district="PPS")
        tech_academy, _ = School.objects.get_or_create(name="Tech Academy", district="PPS")

        # 1. Features
        discord, _ = ProgramFeature.objects.get_or_create(key='discord', name='Discord')
        bg_checks, _ = ProgramFeature.objects.get_or_create(key='background-checks', name='Background Checks')
        andrew_id, _ = ProgramFeature.objects.get_or_create(key='cmu-andrew', name='CMU Andrew ID')

        # 2. Programs
        today = date.today()
        this_year = today.year

        # Past Programs
        p1, _ = Program.objects.get_or_create(
            name=f"Robotics {this_year - 1}",
            year=this_year - 1,
            start_date=date(this_year - 1, 1, 1),
            end_date=date(this_year - 1, 12, 31),
            active=False
        )
        p2, _ = Program.objects.get_or_create(
            name=f"Coding {this_year - 1}",
            year=this_year - 1,
            start_date=date(this_year - 1, 1, 1),
            end_date=date(this_year - 1, 12, 31),
            active=False
        )

        # Present Programs
        p3, _ = Program.objects.get_or_create(
            name=f"Robotics {this_year}",
            year=this_year,
            start_date=date(this_year, 1, 1),
            end_date=date(this_year, 12, 31),
            active=True
        )
        p4, _ = Program.objects.get_or_create(
            name=f"Design {this_year}",
            year=this_year,
            start_date=date(this_year, 1, 1),
            end_date=date(this_year, 12, 31),
            active=True
        )
        p3.features.add(discord, bg_checks, andrew_id)
        p4.features.add(discord)

        # Future Programs
        p5, _ = Program.objects.get_or_create(
            name=f"Robotics {this_year + 1}",
            year=this_year + 1,
            start_date=date(this_year + 1, 1, 1),
            end_date=date(this_year + 1, 12, 31),
            active=True
        )
        p6, _ = Program.objects.get_or_create(
            name=f"AI {this_year + 1}",
            year=this_year + 1,
            start_date=date(this_year + 1, 1, 1),
            end_date=date(this_year + 1, 12, 31),
            active=True
        )

        # 3. Adults (Parents)
        parent1, _ = Adult.objects.get_or_create(
            first_name="Alice",
            last_name="Smith",
            email="swithee+parent1@andrew.cmu.edu",
            is_parent=True
        )
        parent2, _ = Adult.objects.get_or_create(
            first_name="Bob",
            last_name="Smith",
            email="swithee+parent2@andrew.cmu.edu",
            is_parent=True
        )
        parent3, _ = Adult.objects.get_or_create(
            first_name="Charlie",
            last_name="Brown",
            email="swithee+parent3@andrew.cmu.edu",
            is_parent=True
        )
        parent4, _ = Adult.objects.get_or_create(
            first_name="Dana",
            last_name="Brown",
            email="swithee+parent4@andrew.cmu.edu",
            is_parent=True
        )

        # 4. Students
        # Student 1: Enrolled in ALL programs
        student1, _ = Student.objects.get_or_create(
            legal_first_name="John",
            last_name="Smith",
            personal_email="swithee+student1@andrew.cmu.edu",
            school=high_school,
            graduation_year=this_year + 2,
            primary_contact=parent1,
            secondary_contact=parent2
        )
        for p in [p1, p2, p3, p4, p5, p6]:
            Enrollment.objects.get_or_create(student=student1, program=p)

        # Student 2: Enrolled in ALL programs
        student2, _ = Student.objects.get_or_create(
            legal_first_name="Jane",
            last_name="Smith",
            personal_email="swithee+student2@andrew.cmu.edu",
            school=high_school,
            graduation_year=this_year + 4,
            primary_contact=parent1,
            secondary_contact=parent2
        )
        for p in [p1, p2, p3, p4, p5, p6]:
            Enrollment.objects.get_or_create(student=student2, program=p)

        # Student 3: Enrolled only once (Present Robotics)
        student3, _ = Student.objects.get_or_create(
            legal_first_name="Lucy",
            last_name="Brown",
            personal_email="swithee+student3@andrew.cmu.edu",
            school=tech_academy,
            graduation_year=this_year + 1,
            primary_contact=parent3,
            secondary_contact=parent4
        )
        Enrollment.objects.get_or_create(student=student3, program=p3)

        # 5. Mentors
        # Lead Mentor
        Adult.objects.get_or_create(
            first_name="Lead",
            last_name="Mentor",
            email="swithee+lead@andrew.cmu.edu",
            is_mentor=True,
            role="mentor" # There isn't a "lead" role in MENTOR_ROLE_CHOICES, but description says "lead mentor"
        )
        
        # Mentor passing clearances
        Adult.objects.get_or_create(
            first_name="Passing",
            last_name="Mentor",
            email="swithee+passing@andrew.cmu.edu",
            is_mentor=True,
            has_paca_clearance=True,
            has_patch_clearance=True,
            has_fbi_clearance=True,
            pa_clearances_expiration_date=today + timedelta(days=365)
        )

        # Mentor with expiring clearances
        Adult.objects.get_or_create(
            first_name="Expiring",
            last_name="Mentor",
            email="swithee+expiring@andrew.cmu.edu",
            is_mentor=True,
            has_paca_clearance=True,
            has_patch_clearance=True,
            has_fbi_clearance=True,
            pa_clearances_expiration_date=today + timedelta(days=5)
        )

        # 6. Alumni
        alumni1, _ = Adult.objects.get_or_create(
            first_name="Alumni",
            last_name="One",
            email="swithee+alumni1@andrew.cmu.edu",
            is_alumni=True,
            active=False
        )
        alumni2, _ = Adult.objects.get_or_create(
            first_name="Alumni",
            last_name="Two",
            email="swithee+alumni2@andrew.cmu.edu",
            is_alumni=True,
            active=False
        )
        # Alumni are often former students. Let's create Student records for them in past programs.
        alumni_student1, _ = Student.objects.get_or_create(
            legal_first_name="Alumni",
            last_name="One",
            personal_email="swithee+alumni1_student@andrew.cmu.edu",
            school=high_school,
            graduation_year=this_year - 1
        )
        Enrollment.objects.get_or_create(student=alumni_student1, program=p1)

        alumni_student2, _ = Student.objects.get_or_create(
            legal_first_name="Alumni",
            last_name="Two",
            personal_email="swithee+alumni2_student@andrew.cmu.edu",
            school=tech_academy,
            graduation_year=this_year - 1
        )
        Enrollment.objects.get_or_create(student=alumni_student2, program=p2)

        self.stdout.write(self.style.SUCCESS('Successfully seeded database'))
