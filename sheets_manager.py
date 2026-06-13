import os
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

class SheetsManager:
    def __init__(self):
        self.sheet_id = os.getenv("GOOGLE_SHEET_ID")
        self.credentials_path = "credentials.json"
        self.client = self._authenticate()
        self.sheet = self._get_sheet()
        self.emails_cache = set()
        self._load_cache()
        self._ensure_headers()

    def _authenticate(self):
        if not os.path.exists(self.credentials_path):
            raise FileNotFoundError(f"Credentials file {self.credentials_path} not found.")
        
        credentials = Credentials.from_service_account_file(
            self.credentials_path, scopes=SCOPES)
        return gspread.authorize(credentials)

    def _get_sheet(self):
        # Open by ID and get first sheet
        try:
            workbook = self.client.open_by_key(self.sheet_id)
            return workbook.sheet1
        except Exception as e:
            print(f"Error opening sheet: {e}")
            raise

    def _ensure_headers(self):
        headers = [
            # ── Core contact info ───────────────────────────────────
            "Full Name",           # A
            "Email Address",       # B
            "Role/Title",          # C
            "Source",              # D
            "Bio Snippet",         # E
            # ── AI profile extraction ───────────────────────────────
            "Segment",             # F  e.g. indian_designer
            "Pain Points",         # G  pipe-delimited, max 3
            "Current Tools",       # H  pipe-delimited
            "Client Type",         # I  indian_clients / international_clients / both
            "Experience",          # J  junior / mid / senior
            "Best Angle",          # K  gst_pain / ai_generator etc.
            "Is Freelancer",       # L  TRUE / FALSE
            "Confidence",          # M  high / medium / low
            # ── Outreach columns ───────────────────────────────────
            "AI Generated Email",  # N
            "Trigger Angle Used",  # O
            "Profile/Website URL", # P
            "Date Added",          # Q
            "Sent?",               # R
            "Reply?",              # S
            "Signed Up?",          # T
            "Notes",               # U
        ]
        
        # Check if first row is empty
        first_row = self.sheet.row_values(1)
        if not first_row:
            self.sheet.insert_row(headers, 1)

    def _load_cache(self):
        try:
            # Column B is Email Address
            emails = self.sheet.col_values(2)
            # Skip the header
            if emails and emails[0].lower() == "email address":
                emails = emails[1:]
            self.emails_cache = {e.lower().strip() for e in emails if e.strip()}
        except Exception as e:
            print(f"Error loading email cache: {e}")

    def is_duplicate(self, email: str) -> bool:
        if not email:
            return True # Can't add without email
        return email.lower().strip() in self.emails_cache

    def add_contact(
        self,
        contact_data: dict,
        ai_email: str,
        trigger_angle: str,
        extracted_profile: dict = None,
    ) -> bool:
        """
        Appends a new contact to the sheet if it's not a duplicate.
        extracted_profile is the dict returned by profile_extractor.extract_profile().
        If omitted, all profile-extraction columns are left blank.
        Returns True if added, False if duplicate or failed.
        """
        email = contact_data.get('email', '')
        if self.is_duplicate(email):
            print(f"Duplicate email skipped: {email}")
            return False

        # Prepare bio snippet (max 150 chars)
        bio = contact_data.get('bio', '')
        if len(bio) > 150:
            bio = bio[:147] + "..."

        date_added = datetime.now().strftime("%Y-%m-%d")

        # Unpack profile extraction fields (safe defaults if not provided)
        ep = extracted_profile or {}
        is_freelancer_val = ep.get('isFreelancer', '')
        if isinstance(is_freelancer_val, bool):
            is_freelancer_val = 'TRUE' if is_freelancer_val else 'FALSE'

        row = [
            # ── Core contact info ─────────────────────────────────
            contact_data.get('name', ''),          # A  Full Name
            email,                                  # B  Email Address
            contact_data.get('role', ''),           # C  Role/Title
            contact_data.get('source', ''),         # D  Source
            bio,                                    # E  Bio Snippet
            # ── AI profile extraction ─────────────────────────────
            ep.get('segment', ''),                  # F  Segment
            ep.get('painPoints', ''),               # G  Pain Points
            ep.get('currentTools', ''),             # H  Current Tools
            ep.get('clientType', ''),               # I  Client Type
            ep.get('experience', ''),               # J  Experience
            ep.get('bestAngle', ''),                # K  Best Angle
            is_freelancer_val,                      # L  Is Freelancer
            ep.get('confidence', ''),               # M  Confidence
            # ── Outreach columns ──────────────────────────────────
            ai_email,                               # N  AI Generated Email
            trigger_angle,                          # O  Trigger Angle Used
            contact_data.get('url', ''),            # P  Profile/Website URL
            date_added,                             # Q  Date Added
            "",                                     # R  Sent?
            "",                                     # S  Reply?
            "",                                     # T  Signed Up?
            "",                                     # U  Notes
        ]

        try:
            self.sheet.append_row(row)
            self.emails_cache.add(email.lower().strip())
            print(f"Successfully added {email} to Google Sheets.")
            return True
        except Exception as e:
            print(f"Error adding row to sheet: {e}")
            return False
