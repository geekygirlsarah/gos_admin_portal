"""Test package for programs models."""

from .test_adults import AdultModelTests
from .test_finances import FinanceModelTests
from .test_permissions import RolePermissionModelTests
from .test_programs import ProgramModelTests
from .test_students import (
    DemographicModelTests,
    StudentModelTests,
    UnsavedStudentModelTests,
)

__all__ = [
    "AdultModelTests",
    "DemographicModelTests",
    "FinanceModelTests",
    "ProgramModelTests",
    "RolePermissionModelTests",
    "StudentModelTests",
    "UnsavedStudentModelTests",
]
