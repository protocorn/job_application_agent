"""
Profile management mixin.

Field names match the profile_service / frontend convention exactly
(e.g.  "date of birth",  "zip",  "work experience",  "other links")
so that data round-trips correctly through POST /api/profile.
"""

import logging
from launchway.api_client import LaunchwayAPIError
from launchway.cli.utils import Colors

logger = logging.getLogger(__name__)

# ── helpers ──────────────────────────────────────────────────────────────────

def _yn(val) -> str:
    """Render a boolean / string value as Yes / No / the raw string."""
    if val is True  or (isinstance(val, str) and val.lower() in ('yes', 'true', '1')):
        return "Yes"
    if val is False or (isinstance(val, str) and val.lower() in ('no', 'false', '0')):
        return "No"
    return str(val) if val else "Not set"

def _list_str(val) -> str:
    if isinstance(val, list):
        return ", ".join(str(v) for v in val) if val else "—"
    return str(val) if val else "—"

def _ask(prompt: str, current=None) -> str:
    display = f" (current: {current})" if current else ""
    return input(f"  {prompt}{display}: ").strip()

def _ask_list(prompt: str, current: list = None) -> list:
    display = f" (current: {_list_str(current)})" if current else ""
    raw = input(f"  {prompt}{display}\n  (comma-separated, leave blank to keep): ").strip()
    if not raw:
        return current or []
    return [s.strip() for s in raw.split(",") if s.strip()]


# ── mixin ────────────────────────────────────────────────────────────────────

class ProfileMixin:

    # ── menu ──────────────────────────────────────────────────────────────

    def profile_menu(self):
        options = [
            ("View Full Profile",           self.view_profile),
            ("Basic Info",                  self.update_basic_info),
            ("Contact Info",                self.update_contact_info),
            ("Resume URL",                  self.update_resume_url),
            ("Summary / Bio",               self.update_summary),
            ("Education",                   self.update_education),
            ("Work Experience",             self.update_work_experience),
            ("Projects",                    self.update_projects),
            ("Skills",                      self.update_skills),
            ("Cover Letter Template",       self.update_cover_letter),
            ("Job Preferences",             self.update_job_preferences),
            ("EEO / Compliance Info",       self.update_eeo_info),
            ("Other Links / Portfolio",     self.update_other_links),
            ("Back to Main Menu",           None),
        ]

        while True:
            self.clear_screen()
            self.print_header("PROFILE MANAGEMENT")
            for i, (label, _) in enumerate(options, 1):
                print(f"  {i:>2}. {label}")
            print()

            choice = self.get_input(f"Select option (1-{len(options)}): ").strip()
            try:
                idx = int(choice) - 1
                if idx < 0 or idx >= len(options):
                    raise ValueError
            except ValueError:
                self.print_error("Invalid option")
                self.pause()
                continue

            label, fn = options[idx]
            if fn is None:
                break
            fn()

    # ── view ──────────────────────────────────────────────────────────────

    def view_profile(self):
        self.clear_screen()
        self.print_header("YOUR PROFILE")

        if not self.current_profile:
            self.print_warning("No profile data available.")
            self.pause()
            return

        p = self.current_profile

        def section(title):
            print(f"\n{Colors.BOLD}{Colors.OKCYAN}{title}{Colors.ENDC}")

        def row(label, key, fmt=None):
            val = p.get(key, "")
            if fmt:
                val = fmt(val)
            print(f"  {label:<28} {val or '—'}")

        section("Basic Information")
        row("Name",               "first name",  lambda v: f"{v} {p.get('last name','')}")
        row("Email",              "email")
        row("Date of Birth",      "date of birth")
        row("Gender",             "gender")
        row("Nationality",        "nationality")
        row("Preferred Language", "preferred language")

        section("Contact")
        row("Phone",              "phone")
        row("Address",            "address")
        row("City",               "city")
        row("State",              "state")
        row("ZIP",                "zip")
        row("Country",            "country")
        row("LinkedIn",           "linkedin")
        row("GitHub",             "github")

        section("Resume")
        row("Resume URL",         "resume_url")
        row("Source Type",        "resume_source_type")

        summary = p.get("summary", "")
        if summary:
            section("Summary / Bio")
            print(f"  {summary[:300]}")

        education = p.get("education", [])
        if education:
            section("Education")
            for i, edu in enumerate(education, 1):
                print(f"  {i}. {edu.get('degree','')} — {edu.get('institution','')}")
                print(f"     Graduated: {edu.get('graduation_year','')}  |  GPA: {edu.get('gpa','')}")

        work_exp = p.get("work experience", p.get("work_experience", []))
        if work_exp:
            section("Work Experience")
            for i, exp in enumerate(work_exp, 1):
                print(f"  {i}. {exp.get('title','')} at {exp.get('company','')}")
                print(f"     {exp.get('start_date','')} – {exp.get('end_date','Present')}")

        projects = p.get("projects", [])
        if projects:
            section("Projects")
            for i, proj in enumerate(projects, 1):
                techs = _list_str(proj.get("technologies", []))
                print(f"  {i}. {proj.get('name','')}  [{techs}]")

        skills = p.get("skills", {})
        if skills:
            section("Skills")
            for cat, items in skills.items():
                if items:
                    print(f"  {cat:<24} {_list_str(items)}")

        section("Job Preferences")
        row("Willing to Relocate",    "willing to relocate", _yn)
        row("Preferred Location(s)",  "preferred location",  _list_str)

        section("EEO / Compliance")
        row("Visa Status",        "visa status")
        row("Visa Sponsorship",   "visa sponsorship")
        row("Veteran Status",     "veteran status")
        row("Disabilities",       "disabilities", _list_str)

        other_links = p.get("other links", [])
        if other_links:
            section("Other Links")
            for link in other_links:
                label = link.get("label", "") if isinstance(link, dict) else ""
                url   = link.get("url", link)  if isinstance(link, dict) else link
                print(f"  {label+': ' if label else ''}{url}")

        print()
        self.pause()

    # ── save helper ───────────────────────────────────────────────────────

    def _save_profile_changes(self, changes: dict):
        """Merge changes into current_profile and POST to API."""
        self.current_profile = {**(self.current_profile or {}), **changes}
        try:
            self.api.update_profile(self.current_profile)
            self.current_profile = self.api.get_profile()
        except LaunchwayAPIError as e:
            raise RuntimeError(str(e))

    # ── section editors ───────────────────────────────────────────────────

    def update_basic_info(self):
        self.clear_screen()
        self.print_header("BASIC INFO")
        print("  Leave blank to keep the current value.\n")

        p = self.current_profile or {}
        changes = {}

        val = _ask("Date of Birth (DD/MM/YYYY)", p.get("date of birth"))
        if val: changes["date of birth"] = val

        val = _ask("Gender", p.get("gender"))
        if val: changes["gender"] = val

        val = _ask("Nationality", p.get("nationality"))
        if val: changes["nationality"] = val

        val = _ask("Preferred Language", p.get("preferred language"))
        if val: changes["preferred language"] = val

        if not changes:
            self.print_warning("No changes made.")
            self.pause()
            return
        try:
            self._save_profile_changes(changes)
            self.print_success("Basic info updated!")
        except RuntimeError as e:
            self.print_error(str(e))
        self.pause()

    def update_contact_info(self):
        self.clear_screen()
        self.print_header("CONTACT INFO")
        print("  Leave blank to keep the current value.\n")

        p = self.current_profile or {}
        fields = [
            ("phone",   "Phone"),
            ("address", "Address"),
            ("city",    "City"),
            ("state",   "State"),
            ("zip",     "ZIP Code"),
            ("country", "Country"),
            ("country_code", "Country Code (e.g. +1)"),
            ("state_code",   "State Code (e.g. CA)"),
            ("linkedin", "LinkedIn URL"),
            ("github",   "GitHub URL"),
        ]
        changes = {}
        for key, label in fields:
            val = _ask(label, p.get(key))
            if val:
                changes[key] = val

        if not changes:
            self.print_warning("No changes made.")
            self.pause()
            return
        try:
            self._save_profile_changes(changes)
            self.print_success("Contact info updated!")
        except RuntimeError as e:
            self.print_error(str(e))
        self.pause()

    def update_resume_url(self):
        self.clear_screen()
        self.print_header("RESUME URL")

        p = self.current_profile or {}
        print(f"  Current URL: {p.get('resume_url') or '—'}\n")
        print("  Note: Only Google Docs resume URLs are supported in this version.\n")

        url = _ask("New Resume URL (Google Docs sharing link)")
        if not url:
            self.print_warning("No changes made.")
            self.pause()
            return
        try:
            self._save_profile_changes({"resume_url": url, "resume_source_type": "google_doc"})
            self.print_success("Resume URL updated!")
        except RuntimeError as e:
            self.print_error(str(e))
        self.pause()

    def update_summary(self):
        self.clear_screen()
        self.print_header("SUMMARY / BIO")

        p = self.current_profile or {}
        current = p.get("summary", "")
        if current:
            print(f"  Current summary:\n  {current[:300]}\n")
        else:
            print("  No summary set.\n")

        print("  Enter your professional summary (2-4 sentences).")
        print("  This appears in cover letters and helps AI tailor your resume.\n")
        val = input("  Summary: ").strip()
        if not val:
            self.print_warning("No changes made.")
            self.pause()
            return
        try:
            self._save_profile_changes({"summary": val})
            self.print_success("Summary updated!")
        except RuntimeError as e:
            self.print_error(str(e))
        self.pause()

    def update_education(self):
        self.clear_screen()
        self.print_header("EDUCATION")

        p         = self.current_profile or {}
        education = list(p.get("education", []))

        print(f"  You have {len(education)} education entr{'y' if len(education)==1 else 'ies'}.\n")
        print("  1. Add new entry")
        print("  2. View existing entries")
        print("  3. Remove last entry")
        print("  4. Cancel\n")

        choice = self.get_input("Select option: ").strip()

        if choice == "1":
            print()
            degree     = _ask("Degree (e.g. Bachelor's, Master's)")
            institution = _ask("Institution / University")
            grad_year  = _ask("Graduation Year (YYYY or 'Expected YYYY')")
            gpa        = _ask("GPA (optional, e.g. 3.8/4.0)")
            courses_raw = input("  Relevant Courses (comma-separated, optional): ").strip()
            courses    = [c.strip() for c in courses_raw.split(",") if c.strip()] if courses_raw else []

            if not degree or not institution:
                self.print_error("Degree and institution are required.")
                self.pause()
                return

            education.append({
                "degree":          degree,
                "institution":     institution,
                "graduation_year": grad_year,
                "gpa":             gpa or None,
                "relevant_courses": courses,
            })
            try:
                self._save_profile_changes({"education": education})
                self.print_success("Education entry added!")
            except RuntimeError as e:
                self.print_error(str(e))

        elif choice == "2":
            if not education:
                self.print_warning("No entries yet.")
            else:
                print()
                for i, edu in enumerate(education, 1):
                    courses = _list_str(edu.get("relevant_courses", []))
                    print(f"  {i}. {edu.get('degree','')} — {edu.get('institution','')}")
                    print(f"     Year: {edu.get('graduation_year','')}  |  GPA: {edu.get('gpa') or '—'}")
                    if courses and courses != "—":
                        print(f"     Courses: {courses}")

        elif choice == "3":
            if not education:
                self.print_warning("No entries to remove.")
            else:
                removed = education.pop()
                try:
                    self._save_profile_changes({"education": education})
                    self.print_success(f"Removed: {removed.get('degree','')} from {removed.get('institution','')}")
                except RuntimeError as e:
                    self.print_error(str(e))

        self.pause()

    def update_work_experience(self):
        self.clear_screen()
        self.print_header("WORK EXPERIENCE")

        p        = self.current_profile or {}
        work_exp = list(p.get("work experience", p.get("work_experience", [])))

        print(f"  You have {len(work_exp)} work experience entr{'y' if len(work_exp)==1 else 'ies'}.\n")
        print("  1. Add new entry")
        print("  2. View existing entries")
        print("  3. Remove last entry")
        print("  4. Cancel\n")

        choice = self.get_input("Select option: ").strip()

        if choice == "1":
            print()
            title      = _ask("Job Title")
            company    = _ask("Company")
            start_date = _ask("Start Date (MM/YYYY)")
            end_date   = _ask("End Date (MM/YYYY or 'Present')")
            description = input("  Description / responsibilities: ").strip()
            achieve_raw = input("  Key achievements (comma-separated, optional): ").strip()
            achievements = [a.strip() for a in achieve_raw.split(",") if a.strip()] if achieve_raw else []

            if not title or not company:
                self.print_error("Job title and company are required.")
                self.pause()
                return

            work_exp.append({
                "title":        title,
                "company":      company,
                "start_date":   start_date,
                "end_date":     end_date,
                "description":  description,
                "achievements": achievements,
            })
            try:
                self._save_profile_changes({"work experience": work_exp})
                self.print_success("Work experience entry added!")
            except RuntimeError as e:
                self.print_error(str(e))

        elif choice == "2":
            if not work_exp:
                self.print_warning("No entries yet.")
            else:
                print()
                for i, exp in enumerate(work_exp, 1):
                    achievements = _list_str(exp.get("achievements", []))
                    print(f"  {i}. {exp.get('title','')} at {exp.get('company','')}")
                    print(f"     {exp.get('start_date','')} – {exp.get('end_date','Present')}")
                    if exp.get("description"):
                        print(f"     {exp['description'][:120]}")
                    if achievements and achievements != "—":
                        print(f"     Achievements: {achievements[:100]}")

        elif choice == "3":
            if not work_exp:
                self.print_warning("No entries to remove.")
            else:
                removed = work_exp.pop()
                try:
                    self._save_profile_changes({"work experience": work_exp})
                    self.print_success(f"Removed: {removed.get('title','')} at {removed.get('company','')}")
                except RuntimeError as e:
                    self.print_error(str(e))

        self.pause()

    def update_projects(self):
        self.clear_screen()
        self.print_header("PROJECTS")

        p        = self.current_profile or {}
        projects = list(p.get("projects", []))

        print(f"  You have {len(projects)} project entr{'y' if len(projects)==1 else 'ies'}.\n")
        print("  1. Add new project")
        print("  2. View existing projects")
        print("  3. Remove last project")
        print("  4. Cancel\n")

        choice = self.get_input("Select option: ").strip()

        if choice == "1":
            print()
            name        = _ask("Project Name")
            description = input("  Description: ").strip()
            tech_raw    = input("  Technologies used (comma-separated): ").strip()
            technologies = [t.strip() for t in tech_raw.split(",") if t.strip()] if tech_raw else []
            github_url  = _ask("GitHub URL (optional)")
            live_url    = _ask("Live / Demo URL (optional)")
            feats_raw   = input("  Key features (comma-separated, optional): ").strip()
            features    = [f.strip() for f in feats_raw.split(",") if f.strip()] if feats_raw else []

            if not name:
                self.print_error("Project name is required.")
                self.pause()
                return

            projects.append({
                "name":         name,
                "description":  description,
                "technologies": technologies,
                "github_url":   github_url or None,
                "live_url":     live_url or None,
                "features":     features,
            })
            try:
                self._save_profile_changes({"projects": projects})
                self.print_success("Project added!")
            except RuntimeError as e:
                self.print_error(str(e))

        elif choice == "2":
            if not projects:
                self.print_warning("No projects yet.")
            else:
                print()
                for i, proj in enumerate(projects, 1):
                    techs = _list_str(proj.get("technologies", []))
                    print(f"  {i}. {proj.get('name','')}")
                    print(f"     Tech: {techs}")
                    if proj.get("github_url"):
                        print(f"     GitHub: {proj['github_url']}")
                    if proj.get("live_url"):
                        print(f"     Live: {proj['live_url']}")

        elif choice == "3":
            if not projects:
                self.print_warning("No projects to remove.")
            else:
                removed = projects.pop()
                try:
                    self._save_profile_changes({"projects": projects})
                    self.print_success(f"Removed: {removed.get('name','')}")
                except RuntimeError as e:
                    self.print_error(str(e))

        self.pause()

    def update_skills(self):
        self.clear_screen()
        self.print_header("SKILLS")
        print("  Enter comma-separated values. Leave blank to keep current.\n")

        p      = self.current_profile or {}
        skills = dict(p.get("skills", {}))

        categories = [
            ("technical",             "Technical Skills (tools, platforms, etc.)"),
            ("programming_languages", "Programming Languages"),
            ("frameworks",            "Frameworks & Libraries"),
            ("tools",                 "DevOps / Build Tools"),
            ("soft_skills",           "Soft Skills"),
            ("languages",             "Spoken Languages"),
        ]

        changed = False
        for key, label in categories:
            new_list = _ask_list(label, skills.get(key, []))
            if new_list != skills.get(key, []):
                skills[key]  = new_list
                changed      = True

        if not changed:
            self.print_warning("No changes made.")
            self.pause()
            return
        try:
            self._save_profile_changes({"skills": skills})
            self.print_success("Skills updated!")
        except RuntimeError as e:
            self.print_error(str(e))
        self.pause()

    def update_cover_letter(self):
        self.clear_screen()
        self.print_header("COVER LETTER TEMPLATE")

        p = self.current_profile or {}
        current = p.get("cover_letter_template", "")
        if current:
            print(f"  Current template (first 300 chars):\n  {current[:300]}\n")
        else:
            print("  No cover letter template set.\n")

        print("  Enter your cover letter template below.")
        print("  You can use placeholders like {company}, {job_title}, {your_name}.\n")
        print("  (Paste multi-line text, then press Enter on an empty line to finish)\n")

        lines = []
        while True:
            line = input("  > ")
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)

        template = "\n".join(lines).strip()
        if not template:
            self.print_warning("No changes made.")
            self.pause()
            return
        try:
            self._save_profile_changes({"cover_letter_template": template})
            self.print_success("Cover letter template saved!")
        except RuntimeError as e:
            self.print_error(str(e))
        self.pause()

    def update_job_preferences(self):
        self.clear_screen()
        self.print_header("JOB PREFERENCES")
        print("  Leave blank to keep the current value.\n")

        p = self.current_profile or {}

        relocate_current = _yn(p.get("willing to relocate", ""))
        relocate_input   = _ask(f"Willing to Relocate? (yes/no)", relocate_current)
        willing_to_relocate = None
        if relocate_input.lower() in ("yes", "y"):
            willing_to_relocate = True
        elif relocate_input.lower() in ("no", "n"):
            willing_to_relocate = False

        pref_locations = _ask_list(
            "Preferred Job Location(s) (e.g. Remote, New York NY, San Francisco CA)",
            p.get("preferred location", []),
        )

        changes = {}
        if willing_to_relocate is not None:
            changes["willing to relocate"] = willing_to_relocate
        if pref_locations:
            changes["preferred location"] = pref_locations

        if not changes:
            self.print_warning("No changes made.")
            self.pause()
            return
        try:
            self._save_profile_changes(changes)
            self.print_success("Job preferences updated!")
        except RuntimeError as e:
            self.print_error(str(e))
        self.pause()

    def update_eeo_info(self):
        self.clear_screen()
        self.print_header("EEO / COMPLIANCE INFO")
        print("  This information is used to answer optional diversity and")
        print("  compliance questions on job applications.\n")
        print("  Leave blank to keep the current value.\n")

        p = self.current_profile or {}

        val = _ask("Visa Status (e.g. US Citizen, F-1, H-1B, PR, OPT, etc.)", p.get("visa status"))
        visa_status = val or None

        val = _ask("Visa Sponsorship Required? (yes/no/not required)", p.get("visa sponsorship"))
        visa_sponsorship = val or None

        val = _ask("Veteran Status (e.g. Not a veteran, Veteran, etc.)", p.get("veteran status"))
        veteran_status = val or None

        disabilities_list = _ask_list(
            "Disability Status (e.g. No disability, Blind, Deaf — comma-separated)",
            p.get("disabilities", []),
        )

        changes = {}
        if visa_status:          changes["visa status"]     = visa_status
        if visa_sponsorship:     changes["visa sponsorship"] = visa_sponsorship
        if veteran_status:       changes["veteran status"]  = veteran_status
        if disabilities_list:    changes["disabilities"]    = disabilities_list

        if not changes:
            self.print_warning("No changes made.")
            self.pause()
            return
        try:
            self._save_profile_changes(changes)
            self.print_success("EEO / compliance info updated!")
        except RuntimeError as e:
            self.print_error(str(e))
        self.pause()

    def update_other_links(self):
        self.clear_screen()
        self.print_header("OTHER LINKS / PORTFOLIO")

        p           = self.current_profile or {}
        other_links = list(p.get("other links", []))

        print(f"  You have {len(other_links)} additional link(s).\n")
        print("  1. Add a link")
        print("  2. View existing links")
        print("  3. Remove last link")
        print("  4. Cancel\n")

        choice = self.get_input("Select option: ").strip()

        if choice == "1":
            print()
            label = _ask("Label (e.g. Portfolio, Dribbble, Personal Website)")
            url   = _ask("URL (https://...)")
            if not url:
                self.print_error("URL is required.")
                self.pause()
                return
            other_links.append({"label": label, "url": url})
            try:
                self._save_profile_changes({"other links": other_links})
                self.print_success("Link added!")
            except RuntimeError as e:
                self.print_error(str(e))

        elif choice == "2":
            if not other_links:
                self.print_warning("No links added yet.")
            else:
                print()
                for i, link in enumerate(other_links, 1):
                    lbl = link.get("label", "") if isinstance(link, dict) else ""
                    u   = link.get("url", link)  if isinstance(link, dict) else link
                    print(f"  {i}. {lbl+': ' if lbl else ''}{u}")

        elif choice == "3":
            if not other_links:
                self.print_warning("No links to remove.")
            else:
                removed = other_links.pop()
                try:
                    self._save_profile_changes({"other links": other_links})
                    label = removed.get("label", "") if isinstance(removed, dict) else ""
                    self.print_success(f"Removed: {label}")
                except RuntimeError as e:
                    self.print_error(str(e))

        self.pause()
