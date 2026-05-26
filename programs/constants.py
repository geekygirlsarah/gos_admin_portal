STATE_CHOICES = [
    ("AL", "Alabama"),
    ("AK", "Alaska"),
    ("AZ", "Arizona"),
    ("AR", "Arkansas"),
    ("CA", "California"),
    ("CO", "Colorado"),
    ("CT", "Connecticut"),
    ("DE", "Delaware"),
    ("FL", "Florida"),
    ("GA", "Georgia"),
    ("HI", "Hawaii"),
    ("ID", "Idaho"),
    ("IL", "Illinois"),
    ("IN", "Indiana"),
    ("IA", "Iowa"),
    ("KS", "Kansas"),
    ("KY", "Kentucky"),
    ("LA", "Louisiana"),
    ("ME", "Maine"),
    ("MD", "Maryland"),
    ("MA", "Massachusetts"),
    ("MI", "Michigan"),
    ("MN", "Minnesota"),
    ("MS", "Mississippi"),
    ("MO", "Missouri"),
    ("MT", "Montana"),
    ("NE", "Nebraska"),
    ("NV", "Nevada"),
    ("NH", "New Hampshire"),
    ("NJ", "New Jersey"),
    ("NM", "New Mexico"),
    ("NY", "New York"),
    ("NC", "North Carolina"),
    ("ND", "North Dakota"),
    ("OH", "Ohio"),
    ("OK", "Oklahoma"),
    ("OR", "Oregon"),
    ("PA", "Pennsylvania"),
    ("RI", "Rhode Island"),
    ("SC", "South Carolina"),
    ("SD", "South Dakota"),
    ("TN", "Tennessee"),
    ("TX", "Texas"),
    ("UT", "Utah"),
    ("VT", "Vermont"),
    ("VA", "Virginia"),
    ("WA", "Washington"),
    ("WV", "West Virginia"),
    ("WI", "Wisconsin"),
    ("WY", "Wyoming"),
]

RELATIONSHIP_CHOICES = [
    ("parent", "Parent"),
    ("grandparent", "Grandparent"),
    ("pibling", "Pibling (aunt/uncle)"),
    ("sibling", "Sibling"),
    ("guardian", "Guardian"),
    ("family_friend", "Family Friend"),
    ("other", "Other"),
]

TSHIRT_SIZE_CHOICES = [
    ("YXS", "Youth XS"),
    ("YS", "Youth S"),
    ("YM", "Youth M"),
    ("YL", "Youth L"),
    ("YXL", "Youth XL"),
    ("XS", "Adult XS"),
    ("S", "Adult S"),
    ("M", "Adult M"),
    ("L", "Adult L"),
    ("XL", "Adult XL"),
    ("2XL", "Adult 2XL"),
    ("3XL", "Adult 3XL"),
    ("4XL", "Adult 4XL"),
]

MENTOR_ROLE_CHOICES = [
    ("mentor", "Mentor"),
    ("volunteer", "Volunteer"),
    ("chaperone", "Chaperone"),
]

TEAM_TYPES = [
    ("FRC", "FRC"),
    ("FTC", "FTC"),
    ("FLL_CHALLENGE", "FLL Challenge"),
    ("FLL_EXPLORE", "FLL Explore"),
]


# 8-char alphanumeric application IDs using an unambiguous Crockford-style
# alphabet (no 0/O, no 1/I/L). Easy to read aloud over the phone.
APP_ID_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
APP_ID_LENGTH = 8

OTP_LENGTH = 6
OTP_TTL_SECONDS = 15 * 60

GRADE_CHOICES = [(0, "K")] + [(i, str(i)) for i in range(1, 13)]
