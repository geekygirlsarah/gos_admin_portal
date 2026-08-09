from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand

from programs.models import (
    Adult,
    AdultStudentRelationship,
    Enrollment,
    Fee,
    Payment,
    Program,
    ProgramFeature,
    School,
    SlidingScale,
    Student,
)


class Command(BaseCommand):
    help = "Seeds the database with test data for development"

    def handle(self, *args, **options):
        self.stdout.write("Seeding database...")

        today = date.today()
        this_year = today.year

        schools = self._seed_schools()
        features = self._seed_features()
        programs = self._seed_programs(this_year, features)
        adults = self._seed_adults(this_year)
        students = self._seed_students(this_year, schools, adults)

        enrollments = self._seed_enrollments(programs, students, today)
        self._seed_fees(programs, today)
        self._seed_sliding_scales(programs, students, today)
        self._seed_payments(enrollments, programs, today)

        self.stdout.write(self.style.SUCCESS("Successfully seeded database"))

    def _seed_schools(self):
        school_data = [
            {"name": "Pittsburgh Science Magnet", "district": "PPS"},
            {"name": "Steel Valley High", "district": "Steel Valley"},
            {"name": "Riverview STEM Academy", "district": "Riverview"},
            {"name": "Three Rivers Charter", "district": "Charter"},
        ]
        schools = []
        for school in school_data:
            school_obj, _ = School.objects.update_or_create(
                name=school["name"], defaults=school
            )
            schools.append(school_obj)
        return schools

    def _seed_features(self):
        feature_data = [
            {
                "key": "discord",
                "name": "Discord",
                "description": "Enable Discord handle collection.",
                "display_order": 10,
            },
            {
                "key": "background-checks",
                "name": "Background Checks",
                "description": "Enable background clearance fields.",
                "display_order": 20,
            },
            {
                "key": "cmu-andrew",
                "name": "CMU Andrew ID",
                "description": "Enable Andrew ID fields.",
                "display_order": 30,
            },
            {
                "key": "tshirt-size",
                "name": "T-shirt Sizes",
                "description": "Enable T-shirt size field collection.",
                "display_order": 40,
            },
        ]

        features_by_key = {}
        for feature in feature_data:
            feature_obj, _ = ProgramFeature.objects.update_or_create(
                key=feature["key"], defaults=feature
            )
            features_by_key[feature_obj.key] = feature_obj
        return features_by_key

    def _seed_programs(self, this_year, features):
        program_blueprints = [
            {
                "name": f"Astro Robotics {this_year - 2}",
                "description": "Archived aerospace robotics workshop season.",
                "active": False,
                "start_date": date(this_year - 2, 1, 15),
                "end_date": date(this_year - 2, 5, 31),
                "cost": "$325",
                "grade_range_start": 7,
                "grade_range_end": 12,
                "feature_keys": ["discord", "background-checks"],
            },
            {
                "name": f"Bioengineering Builders {this_year - 2}",
                "description": "Archived biomedical design challenge.",
                "active": False,
                "start_date": date(this_year - 2, 8, 20),
                "end_date": date(this_year - 2, 12, 10),
                "cost": "$280",
                "grade_range_start": 6,
                "grade_range_end": 11,
                "feature_keys": ["discord"],
            },
            {
                "name": f"Quantum Makers {this_year - 1}",
                "description": "Archived hands-on quantum concepts program.",
                "active": False,
                "start_date": date(this_year - 1, 2, 1),
                "end_date": date(this_year - 1, 6, 15),
                "cost": "$350",
                "grade_range_start": 8,
                "grade_range_end": 12,
                "feature_keys": ["discord", "cmu-andrew"],
            },
            {
                "name": f"Clean Energy Cadets {this_year - 1}",
                "description": "Archived climate-tech build and outreach program.",
                "active": False,
                "start_date": date(this_year - 1, 9, 1),
                "end_date": date(this_year - 1, 12, 20),
                "cost": "$260",
                "grade_range_start": 5,
                "grade_range_end": 10,
                "feature_keys": ["discord", "tshirt-size"],
            },
            {
                "name": f"Girls of Steel FRC {this_year}",
                "description": "Current flagship FIRST Robotics Competition team season.",
                "active": True,
                "start_date": date(this_year, 1, 1),
                "end_date": date(this_year, 10, 30),
                "cost": "$500",
                "grade_range_start": 9,
                "grade_range_end": 12,
                "feature_keys": [
                    "discord",
                    "background-checks",
                    "cmu-andrew",
                    "tshirt-size",
                ],
            },
            {
                "name": f"AI + Vision Robotics {this_year}",
                "description": "Current computer vision and autonomous controls cohort.",
                "active": True,
                "start_date": date(this_year, 3, 1),
                "end_date": date(this_year, 8, 15),
                "cost": "$420",
                "grade_range_start": 8,
                "grade_range_end": 12,
                "feature_keys": ["discord", "cmu-andrew"],
            },
            {
                "name": f"Mechanical Design Lab {this_year}",
                "description": "Current CAD and fabrication intensive.",
                "active": True,
                "start_date": date(this_year, 6, 15),
                "end_date": date(this_year, 11, 30),
                "cost": "$360",
                "grade_range_start": 7,
                "grade_range_end": 12,
                "feature_keys": ["discord", "tshirt-size"],
            },
            {
                "name": f"STEM Outreach Ambassadors {this_year}",
                "description": "Current community outreach and mentoring program.",
                "active": True,
                "start_date": date(this_year, 2, 15),
                "end_date": date(this_year, 12, 15),
                "cost": "$200",
                "grade_range_start": 6,
                "grade_range_end": 12,
                "feature_keys": ["discord"],
            },
            {
                "name": f"Aerospace Systems {this_year + 1}",
                "description": "Upcoming launch systems and controls program.",
                "active": True,
                "start_date": date(this_year + 1, 1, 10),
                "end_date": date(this_year + 1, 5, 20),
                "cost": "$390",
                "grade_range_start": 8,
                "grade_range_end": 12,
                "feature_keys": ["discord", "background-checks"],
            },
            {
                "name": f"Biomedical Robotics {this_year + 1}",
                "description": "Upcoming assistive robotics design cohort.",
                "active": True,
                "start_date": date(this_year + 1, 3, 1),
                "end_date": date(this_year + 1, 8, 1),
                "cost": "$410",
                "grade_range_start": 7,
                "grade_range_end": 12,
                "feature_keys": ["discord", "cmu-andrew"],
            },
            {
                "name": f"Data Science for Robotics {this_year + 1}",
                "description": "Upcoming analytics and telemetry program.",
                "active": True,
                "start_date": date(this_year + 1, 6, 1),
                "end_date": date(this_year + 1, 11, 20),
                "cost": "$340",
                "grade_range_start": 6,
                "grade_range_end": 11,
                "feature_keys": ["discord", "tshirt-size"],
            },
            {
                "name": f"Climate Tech Innovators {this_year + 1}",
                "description": "Upcoming sustainability-focused engineering cohort.",
                "active": True,
                "start_date": date(this_year + 1, 9, 1),
                "end_date": date(this_year + 1, 12, 15),
                "cost": "$275",
                "grade_range_start": 5,
                "grade_range_end": 10,
                "feature_keys": ["discord"],
            },
        ]

        programs = []
        for blueprint in program_blueprints:
            program_fields = {
                "description": blueprint["description"],
                "active": blueprint["active"],
                "start_date": blueprint["start_date"],
                "end_date": blueprint["end_date"],
                "cost": blueprint["cost"],
                "grade_range_start": blueprint["grade_range_start"],
                "grade_range_end": blueprint["grade_range_end"],
            }
            program_obj, _ = Program.objects.update_or_create(
                name=blueprint["name"], defaults=program_fields
            )
            program_obj.features.set(
                [features[key] for key in blueprint["feature_keys"] if key in features]
            )
            programs.append(program_obj)

        return programs

    def _seed_adults(self, this_year):
        parent_data = [
            ("Marie", "Curie"),
            ("Rosalind", "Franklin"),
            ("Katherine", "Johnson"),
            ("Ada", "Lovelace"),
            ("Grace", "Hopper"),
            ("Chien-Shiung", "Wu"),
            ("Lise", "Meitner"),
            ("Emmy", "Noether"),
            ("Rita", "LeviMontalcini"),
            ("Mae", "Jemison"),
            ("Jane", "Goodall"),
            ("Tu", "Youyou"),
        ]

        parents = []
        for idx, (first_name, last_name) in enumerate(parent_data, start=1):
            email = f"seed.parent{idx}@gos.example"
            parent, _ = Adult.objects.update_or_create(
                personal_email=email,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_parent": True,
                    "is_mentor": False,
                    "is_alumni": False,
                    "can_receive_texts": idx % 2 == 0,
                    "email_updates": idx % 3 != 0,
                    "phone_number": f"412555{1000 + idx}",
                },
            )
            parents.append(parent)

        mentor_data = [
            {
                "first_name": "Sally",
                "last_name": "Ride",
                "email": "seed.mentor1@gos.example",
                "role": "mentor",
                "has_paca_clearance": True,
                "has_patch_clearance": True,
                "has_fbi_clearance": True,
                "pa_clearances_expiration_date": date(this_year + 1, 12, 31),
            },
            {
                "first_name": "Barbara",
                "last_name": "McClintock",
                "email": "seed.mentor2@gos.example",
                "role": "volunteer",
                "has_paca_clearance": True,
                "has_patch_clearance": True,
                "has_fbi_clearance": False,
                "pa_clearances_expiration_date": date(this_year + 1, 12, 31),
            },
            {
                "first_name": "Hypatia",
                "last_name": "Alexandria",
                "email": "seed.mentor3@gos.example",
                "role": "chaperone",
                "has_paca_clearance": True,
                "has_patch_clearance": False,
                "has_fbi_clearance": False,
                "pa_clearances_expiration_date": date(this_year + 1, 12, 31),
            },
            {
                "first_name": "Vera",
                "last_name": "Rubin",
                "email": "seed.mentor4@gos.example",
                "role": "mentor",
                "has_paca_clearance": True,
                "has_patch_clearance": True,
                "has_fbi_clearance": True,
                "pa_clearances_expiration_date": date(this_year, 11, 1),
            },
        ]

        mentors = []
        for mentor_info in mentor_data:
            mentor, _ = Adult.objects.update_or_create(
                personal_email=mentor_info["email"],
                defaults={
                    "first_name": mentor_info["first_name"],
                    "last_name": mentor_info["last_name"],
                    "is_parent": False,
                    "is_mentor": True,
                    "is_alumni": False,
                    "role": mentor_info["role"],
                    "start_year": this_year - 3,
                    "has_paca_clearance": mentor_info["has_paca_clearance"],
                    "has_patch_clearance": mentor_info["has_patch_clearance"],
                    "has_fbi_clearance": mentor_info["has_fbi_clearance"],
                    "pa_clearances_expiration_date": mentor_info[
                        "pa_clearances_expiration_date"
                    ],
                    "email_updates": True,
                },
            )
            mentors.append(mentor)

        alumni_data = [
            {
                "first_name": "Hedy",
                "last_name": "Lamarr",
                "personal_email": "seed.alumni1@gos.example",
                "college": "Carnegie Mellon University",
                "field_of_study": "Electrical Engineering",
            },
            {
                "first_name": "Dorothy",
                "last_name": "Vaughan",
                "personal_email": "seed.alumni2@gos.example",
                "college": "University of Pittsburgh",
                "field_of_study": "Computer Science",
            },
        ]

        alumni = []
        for alumnus in alumni_data:
            alum, _ = Adult.objects.update_or_create(
                personal_email=alumnus["personal_email"],
                defaults={
                    "first_name": alumnus["first_name"],
                    "last_name": alumnus["last_name"],
                    "is_parent": False,
                    "is_mentor": False,
                    "is_alumni": True,
                    "active": True,
                    "college": alumnus["college"],
                    "field_of_study": alumnus["field_of_study"],
                    "ok_to_contact": True,
                },
            )
            alumni.append(alum)

        return {
            "parents": parents,
            "mentors": mentors,
            "alumni": alumni,
        }

    def _seed_students(self, this_year, schools, adults):
        students = []
        first_names = [
            "Ava",
            "Mia",
            "Zoe",
            "Lila",
            "Nora",
            "Ruby",
            "Ivy",
            "Eden",
            "Luna",
            "Aria",
            "Skye",
            "Cora",
        ]

        # Re-use and rotate family last names to create obvious sibling groups.
        family_last_names = [
            "Curie",
            "Franklin",
            "Johnson",
            "Lovelace",
            "Hopper",
            "Wu",
        ]

        parent_pool = adults["parents"]
        for idx, first_name in enumerate(first_names):
            family_index = idx // 2
            last_name = family_last_names[family_index]
            primary_contact = parent_pool[(family_index * 2) % len(parent_pool)]
            secondary_contact = parent_pool[(family_index * 2 + 1) % len(parent_pool)]
            graduation_year = this_year + (idx % 6) + 1

            student, _ = Student.objects.update_or_create(
                personal_email=f"seed.student{idx + 1}@gos.example",
                defaults={
                    "legal_first_name": first_name,
                    "first_name": first_name,
                    "last_name": last_name,
                    "school": schools[idx % len(schools)],
                    "graduation_year": graduation_year,
                    "on_discord": idx % 2 == 0,
                    "discord_handle": f"{first_name.lower()}.{last_name.lower()}",
                },
            )

            # Primary/secondary are relationship-row pointers now; the setter
            # keeps the through row and pointer in sync.
            student.primary_contact = primary_contact
            student.secondary_contact = secondary_contact
            student.save(
                update_fields=[
                    "primary_contact_relationship",
                    "secondary_contact_relationship",
                ]
            )

            AdultStudentRelationship.objects.get_or_create(
                adult=primary_contact,
                student=student,
                defaults={"relationship_to_student": "parent"},
            )
            AdultStudentRelationship.objects.get_or_create(
                adult=secondary_contact,
                student=student,
                defaults={"relationship_to_student": "parent"},
            )
            students.append(student)

        return students

    def _seed_enrollments(self, programs, students, today):
        past_programs = [p for p in programs if p.end_date and p.end_date < today]
        current_programs = [
            p
            for p in programs
            if p.start_date and p.end_date and p.start_date <= today <= p.end_date
        ]
        future_programs = [p for p in programs if p.start_date and p.start_date > today]

        enrollments = []
        for idx, student in enumerate(students):
            picks = [
                past_programs[idx % len(past_programs)],
                current_programs[idx % len(current_programs)],
                current_programs[(idx + 1) % len(current_programs)],
                future_programs[idx % len(future_programs)],
            ]
            for program in picks:
                enrollment, _ = Enrollment.objects.get_or_create(
                    student=student,
                    program=program,
                    defaults={
                        "active": program.start_date <= today <= program.end_date
                    },
                )
                enrollments.append(enrollment)

        return enrollments

    def _seed_fees(self, programs, today):
        for program in programs:
            registration_amount = Decimal("175.00")
            materials_amount = Decimal("225.00")
            if program.start_date and program.start_date > today:
                registration_amount = Decimal("200.00")
                materials_amount = Decimal("240.00")

            Fee.objects.update_or_create(
                program=program,
                name="Registration",
                defaults={
                    "amount": registration_amount,
                    "effective_date": program.start_date,
                    "due_date": program.start_date,
                },
            )
            Fee.objects.update_or_create(
                program=program,
                name="Materials",
                defaults={
                    "amount": materials_amount,
                    "effective_date": program.start_date,
                    "due_date": program.start_date,
                },
            )

    def _seed_sliding_scales(self, programs, students, today):
        current_programs = [
            p
            for p in programs
            if p.start_date and p.end_date and p.start_date <= today <= p.end_date
        ]
        if not current_programs:
            return

        primary_program = current_programs[0]
        discount_values = [
            Decimal("10.00"),
            Decimal("15.00"),
            Decimal("20.00"),
            Decimal("25.00"),
            Decimal("35.00"),
            Decimal("50.00"),
        ]
        for idx, discount in enumerate(discount_values):
            is_pending = idx % 4 == 0
            SlidingScale.objects.update_or_create(
                student=students[idx],
                defaults={
                    "percent": discount,
                    "date": primary_program.start_date,
                    "family_size": 3 + idx,
                    "adjusted_gross_income": Decimal("30000.00") + Decimal(idx * 4500),
                    "status": (
                        SlidingScale.STATUS_PENDING
                        if is_pending
                        else SlidingScale.STATUS_APPROVED
                    ),
                    "notes": "Seeded income-based discount",
                },
            )

    def _seed_payments(self, enrollments, programs, today):
        current_program_ids = {
            p.id
            for p in programs
            if p.start_date and p.end_date and p.start_date <= today <= p.end_date
        }
        future_program_ids = {
            p.id for p in programs if p.start_date and p.start_date > today
        }

        for idx, enrollment in enumerate(enrollments):
            if (
                enrollment.program_id not in current_program_ids
                and enrollment.program_id not in future_program_ids
            ):
                continue

            status_selector = idx % 3
            registration_amount = (
                Decimal("175.00")
                if enrollment.program_id in current_program_ids
                else Decimal("200.00")
            )
            materials_amount = (
                Decimal("225.00")
                if enrollment.program_id in current_program_ids
                else Decimal("240.00")
            )
            paid_on = enrollment.program.start_date or today

            if status_selector == 0:
                Payment.objects.update_or_create(
                    student=enrollment.student,
                    program=enrollment.program,
                    paid_on=paid_on,
                    amount=registration_amount,
                    paid_via="check",
                    defaults={
                        "check_number": 5000 + idx,
                        "notes": "Seed: first installment paid",
                    },
                )
                Payment.objects.update_or_create(
                    student=enrollment.student,
                    program=enrollment.program,
                    paid_on=paid_on,
                    amount=materials_amount,
                    paid_via="credit_card",
                    defaults={"notes": "Seed: second installment paid"},
                )
            elif status_selector == 1:
                Payment.objects.update_or_create(
                    student=enrollment.student,
                    program=enrollment.program,
                    paid_on=paid_on,
                    amount=Decimal("150.00"),
                    paid_via="cash",
                    defaults={"notes": "Seed: partial payment"},
                )
