"""
Systematic Resume Tailoring Engine
Two-phase approach: Validation → Execution
No wasteful iterations - only one editing pass with optional overflow recovery.
"""

from typing import Dict, List, Any, Tuple, Optional
import json


# ============================================================
# PHASE 1: KEYWORD VALIDATION & SKILL FEASIBILITY
# ============================================================

class KeywordValidator:
    """Phase 1: Validate which keywords user actually has experience with."""

    def __init__(self, mimikree_integration):
        self.mimikree = mimikree_integration

    def execute_phase_1(
        self,
        job_description: str,
        job_keywords: List[str],
        resume_text: str
    ) -> Dict[str, Any]:
        """
        Phase 1: Determine keyword feasibility BEFORE making any edits.

        Returns:
            {
                'feasible_keywords': [...],  # User has experience with these
                'missing_keywords': [...],   # User lacks these skills
                'mimikree_evidence': {...},  # Detailed evidence from Mimikree
                'max_possible_ats_score': float,  # Best ATS score achievable
                'should_proceed': bool  # Whether tailoring is worth it
            }
        """
        print("\n" + "="*60)
        print("PHASE 1: KEYWORD VALIDATION & SKILL FEASIBILITY")
        print("="*60)

        # Step 1: Ask Mimikree about ALL keywords
        print(f"\n📋 Validating {len(job_keywords)} keywords with Mimikree...")
        keyword_evidence = self._query_mimikree_for_keywords(job_keywords)

        # Step 2: Classify keywords into feasible vs missing
        feasible = []
        missing = []

        for keyword in job_keywords:
            evidence = keyword_evidence.get(keyword, {})
            has_evidence = evidence.get('has_experience', False)

            if has_evidence:
                feasible.append(keyword)
                print(f"   ✓ {keyword}: User has experience")
            else:
                missing.append(keyword)
                print(f"   ✗ {keyword}: No evidence found")

        # Step 3: Calculate maximum possible ATS score
        # Formula: Start at 100, lose 15 points per missing keyword, 10 for placement
        max_ats_score = 100
        max_ats_score -= len(missing) * 15  # Missing keywords
        if len(feasible) < 3:
            max_ats_score -= 10  # Poor keyword placement
        max_ats_score = max(0, max_ats_score)

        # Step 4: Decide if tailoring is worth it
        should_proceed = len(feasible) >= len(missing)  # At least 50% match

        print(f"\n📊 FEASIBILITY ANALYSIS:")
        print(f"   Feasible keywords: {len(feasible)}/{len(job_keywords)}")
        print(f"   Missing keywords: {len(missing)}/{len(job_keywords)}")
        print(f"   Max possible ATS score: {max_ats_score}/100")
        print(f"   Recommendation: {'✅ PROCEED' if should_proceed else '⚠️  LOW MATCH'}")

        return {
            'feasible_keywords': feasible,
            'missing_keywords': missing,
            'keyword_evidence': keyword_evidence,
            'max_possible_ats_score': max_ats_score,
            'should_proceed': should_proceed,
            'match_percentage': len(feasible) / len(job_keywords) * 100 if job_keywords else 0
        }

    def _query_mimikree_for_keywords(self, keywords: List[str]) -> Dict[str, Any]:
        """Query Mimikree for evidence of each keyword."""
        questions = []

        for keyword in keywords:
            questions.append(
                f"Do you have experience with {keyword}? "
                f"Provide specific examples with metrics if available."
            )

        # Batch query Mimikree
        responses = self.mimikree.batch_query(questions)

        # Parse responses to determine if user has experience
        evidence = {}
        for i, keyword in enumerate(keywords):
            response = responses[i] if i < len(responses) else ""

            # Simple heuristic: if response has metrics or specific details, user has experience
            has_experience = (
                len(response) > 50 and  # Substantial response
                (any(char.isdigit() for char in response) or  # Has metrics
                 'experience' in response.lower() or
                 'project' in response.lower())
            )

            evidence[keyword] = {
                'has_experience': has_experience,
                'response': response,
                'confidence': 'high' if len(response) > 100 else 'low'
            }

        return evidence


# ============================================================
# PHASE 2: SYSTEMATIC SECTION-WISE EDITING
# ============================================================

class SectionIdentifier:
    """Identify resume sections and their boundaries."""

    @staticmethod
    def identify_sections(line_metadata: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Identify resume sections (Profile, Experience, Projects, Skills, etc.).

        Returns list of sections:
            {
                'name': 'EXPERIENCE',
                'start_line': 5,
                'end_line': 20,
                'lines': [...],  # Line metadata for this section
                'type': 'experience' | 'projects' | 'skills' | 'education' | 'other'
            }
        """
        sections = []
        current_section = None

        # Common section headers
        SECTION_PATTERNS = {
            'profile': r'^(PROFILE|SUMMARY|OBJECTIVE)',
            'experience': r'^(EXPERIENCE|WORK EXPERIENCE|PROFESSIONAL EXPERIENCE)',
            'projects': r'^(PROJECTS|KEY PROJECTS|NOTABLE PROJECTS)',
            'skills': r'^(SKILLS|TECHNICAL SKILLS|CORE COMPETENCIES)',
            'education': r'^(EDUCATION|ACADEMIC BACKGROUND)',
            'achievements': r'^(ACHIEVEMENTS|AWARDS|HONORS)',
            'publications': r'^(PUBLICATIONS|RESEARCH)',
        }

        for i, line in enumerate(line_metadata):
            text = line['text'].strip().upper()

            # Check if this is a section header
            is_header = False
            section_type = 'other'

            for stype, pattern in SECTION_PATTERNS.items():
                import re
                if re.match(pattern, text):
                    is_header = True
                    section_type = stype
                    break

            if is_header:
                # Save previous section
                if current_section:
                    current_section['end_line'] = i - 1
                    sections.append(current_section)

                # Start new section
                current_section = {
                    'name': text,
                    'type': section_type,
                    'start_line': i,
                    'end_line': None,
                    'lines': []
                }
            elif current_section:
                current_section['lines'].append(line)

        # Close last section
        if current_section:
            current_section['end_line'] = len(line_metadata) - 1
            sections.append(current_section)

        return sections


class ProjectReplacementEngine:
    """Handle complete project replacements (title, dates, description)."""

    @staticmethod
    def parse_project_structure(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Parse project entries from lines.

        Each project has:
        - Title line (bold, may have URL)
        - Date/location line (may be on same line or next line)
        - Description bullets (list items)
        """
        projects = []
        i = 0

        while i < len(lines):
            line = lines[i]

            # Heuristic: Project title is usually bold or has certain patterns
            if line.get('bullet_level', 0) == 0 and (
                len(line['text']) < 150 or  # Likely a title, not description
                ' – ' in line['text'] or  # Common pattern: "Project Name – Description"
                ' | ' in line['text']  # Common pattern: "Project Name | URL"
            ):
                # This looks like a project title
                project = {
                    'title_line': line,
                    'title_line_index': i,
                    'date_line': None,
                    'date_line_index': None,
                    'description_lines': [],
                    'description_line_indices': []
                }

                # Next line might be dates/location
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    # Check if it looks like a date line (has months, years, locations)
                    if any(month in next_line['text'] for month in [
                        'January', 'February', 'March', 'April', 'May', 'June',
                        'July', 'August', 'September', 'October', 'November', 'December',
                        'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
                    ]) or '202' in next_line['text']:  # Year pattern
                        project['date_line'] = next_line
                        project['date_line_index'] = i + 1
                        i += 1

                # Collect description bullets
                i += 1
                while i < len(lines):
                    if lines[i].get('bullet_level', 0) > 0:
                        # This is a bullet point
                        project['description_lines'].append(lines[i])
                        project['description_line_indices'].append(i)
                        i += 1
                    else:
                        # Next project or section
                        break

                projects.append(project)
            else:
                i += 1

        return projects

    @staticmethod
    def create_project_replacement(
        original_project: Dict[str, Any],
        new_project_data: Dict[str, Any],
        mimikree_evidence: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create replacement instructions for a complete project.

        Args:
            original_project: Parsed project structure
            new_project_data: New project info from Mimikree
            mimikree_evidence: Evidence supporting the new project

        Returns:
            {
                'title_replacement': {...},
                'date_replacement': {...},
                'description_replacements': [...]
            }
        """
        # This will be implemented to handle complete project swaps
        pass


class SystematicEditor:
    """Phase 2: Execute systematic top-to-bottom editing."""

    def __init__(self, docs_service, document_id):
        self.docs_service = docs_service
        self.document_id = document_id
        self.section_identifier = SectionIdentifier()
        self.project_engine = ProjectReplacementEngine()

    def execute_phase_2(
        self,
        line_metadata: List[Dict[str, Any]],
        validation_results: Dict[str, Any],
        space_borrowing_plan: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Phase 2: Make systematic edits section by section, top to bottom.

        Args:
            line_metadata: Document structure
            validation_results: Results from Phase 1
            space_borrowing_plan: Optional plan for redistributing space

        Returns:
            {
                'edits_made': [...],
                'sections_modified': [...],
                'success': bool,
                'requires_overflow_recovery': bool
            }
        """
        print("\n" + "="*60)
        print("PHASE 2: SYSTEMATIC SECTION-WISE EDITING")
        print("="*60)

        # Step 1: Identify sections
        sections = self.section_identifier.identify_sections(line_metadata)
        print(f"\n📂 Identified {len(sections)} sections:")
        for section in sections:
            print(f"   • {section['name']} ({len(section['lines'])} lines)")

        # Step 2: Process each section in order (top to bottom)
        all_edits = []

        for section in sections:
            print(f"\n🔧 Processing section: {section['name']}")

            if section['type'] == 'profile':
                edits = self._edit_profile_section(section, validation_results)
            elif section['type'] == 'experience':
                edits = self._edit_experience_section(section, validation_results, space_borrowing_plan)
            elif section['type'] == 'projects':
                edits = self._edit_projects_section(section, validation_results, space_borrowing_plan)
            elif section['type'] == 'skills':
                edits = self._edit_skills_section(section, validation_results)
            else:
                edits = self._edit_other_section(section, validation_results)

            all_edits.extend(edits)
            print(f"   ✓ Made {len(edits)} edit(s)")

        # Step 3: Apply all edits to document
        print(f"\n📝 Applying {len(all_edits)} total edits to document...")
        success = self._apply_edits(all_edits)

        # Step 4: Check if page overflow occurred
        requires_recovery = self._check_page_overflow()

        return {
            'edits_made': all_edits,
            'sections_modified': [s['name'] for s in sections],
            'success': success,
            'requires_overflow_recovery': requires_recovery
        }

    def _edit_profile_section(
        self,
        section: Dict[str, Any],
        validation: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Edit profile/summary section with feasible keywords."""
        edits = []

        # For profile, we want to naturally incorporate feasible keywords
        # This is typically 1-3 lines of summary text
        for line in section['lines']:
            # Use Gemini to rewrite with feasible keywords
            prompt = f"""Rewrite this profile line to naturally include these keywords: {validation['feasible_keywords'][:5]}

Original: {line['text']}

Requirements:
- Keep same length (±5 characters)
- Be truthful and professional
- Natural, not keyword-stuffed
- Maintain any formatting markers (bold, italic)

Return only the rewritten text."""

            # This would call Gemini and create an edit
            # edits.append({...})

        return edits

    def _edit_experience_section(
        self,
        section: Dict[str, Any],
        validation: Dict[str, Any],
        space_plan: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Edit experience section with space borrowing."""
        edits = []

        # Parse experience entries (company, role, dates, bullets)
        # Apply space borrowing: condense low-relevance, expand high-relevance
        # Focus on incorporating feasible keywords

        return edits

    def _edit_projects_section(
        self,
        section: Dict[str, Any],
        validation: Dict[str, Any],
        space_plan: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Edit projects section with complete project replacements.

        This is the most complex section because we may:
        1. Keep existing projects but rewrite descriptions
        2. Replace entire projects with more relevant ones from Mimikree
        3. Reorder projects by relevance
        """
        edits = []

        # Parse project structure
        projects = self.project_engine.parse_project_structure(section['lines'])

        print(f"      Found {len(projects)} project(s)")

        # For each project, decide: keep, modify, or replace
        for project in projects:
            # Calculate relevance score
            relevance = self._calculate_project_relevance(
                project,
                validation['feasible_keywords']
            )

            if relevance < 20:
                # Low relevance - candidate for replacement
                print(f"      • Low relevance project: {project['title_line']['text'][:50]}...")
                # Query Mimikree for better alternative
            else:
                # Keep but enhance with keywords
                print(f"      • Keeping project: {project['title_line']['text'][:50]}...")

        return edits

    def _edit_skills_section(
        self,
        section: Dict[str, Any],
        validation: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Edit skills section to highlight feasible keywords."""
        edits = []

        # Skills section is usually comma-separated lists
        # We want to ensure feasible keywords appear prominently

        return edits

    def _edit_other_section(
        self,
        section: Dict[str, Any],
        validation: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Edit other sections (education, achievements, etc.)."""
        return []

    def _calculate_project_relevance(
        self,
        project: Dict[str, Any],
        keywords: List[str]
    ) -> float:
        """Calculate how relevant a project is to the job."""
        score = 0

        # Check title
        title = project['title_line']['text'].lower()
        for keyword in keywords:
            if keyword.lower() in title:
                score += 10

        # Check description
        for desc_line in project['description_lines']:
            text = desc_line['text'].lower()
            for keyword in keywords:
                if keyword.lower() in text:
                    score += 5

        return score

    def _apply_edits(self, edits: List[Dict[str, Any]]) -> bool:
        """Apply all edits to the Google Doc."""
        # This would use the Google Docs API to make replacements
        return True

    def _check_page_overflow(self) -> bool:
        """Check if edits caused page overflow."""
        # Export to PDF and check page count
        return False


# ============================================================
# OVERFLOW RECOVERY (ONLY IF NEEDED)
# ============================================================

class OverflowRecoveryEngine:
    """One-time overflow recovery using space borrowing."""

    def __init__(self, docs_service, document_id):
        self.docs_service = docs_service
        self.document_id = document_id

    def recover_from_overflow(
        self,
        line_metadata: List[Dict[str, Any]],
        keywords: List[str],
        target_pages: int
    ) -> Dict[str, Any]:
        """
        One-time recovery from page overflow using intelligent space borrowing.

        Strategy:
        1. Identify longest low-relevance bullets
        2. Condense them aggressively
        3. Do NOT expand anything else
        4. Goal: Get back to target page count
        """
        print("\n" + "="*60)
        print("OVERFLOW RECOVERY: SPACE CONDENSING")
        print("="*60)

        from space_borrowing import (
            calculate_relevance_scores,
            identify_space_borrowing_opportunities
        )

        # Calculate relevance
        scored_lines = calculate_relevance_scores(line_metadata, keywords, "")

        # Find condensing opportunities
        opportunities = identify_space_borrowing_opportunities(scored_lines, keywords)

        # Sort by: longest + lowest relevance first
        donors = sorted(
            opportunities['donor_lines'],
            key=lambda x: (x['visual_lines'], -x['relevance']),
            reverse=True
        )

        print(f"\n🎯 Found {len(donors)} lines to condense:")
        for i, donor in enumerate(donors[:5], 1):
            print(f"   {i}. Line {donor['line_number']}: {donor['visual_lines']} visual lines")
            print(f"      Relevance: {donor['relevance']}/100")
            print(f"      Text: {donor['text_preview']}")

        # Create condensing instructions
        condense_targets = donors[:5]  # Top 5 culprits

        # Use Gemini to aggressively condense ONLY these lines
        # Goal: reduce by 1 visual line each

        return {
            'lines_condensed': len(condense_targets),
            'estimated_lines_freed': sum(d['can_remove_lines'] for d in condense_targets),
            'success': True
        }


# ============================================================
# MAIN ORCHESTRATOR
# ============================================================

class SystematicResumeTailoring:
    """Main orchestrator for the two-phase systematic approach."""

    def __init__(self, docs_service, drive_service, mimikree_integration):
        self.docs_service = docs_service
        self.drive_service = drive_service
        self.validator = KeywordValidator(mimikree_integration)
        self.editor = None  # Will be initialized with document ID
        self.recovery_engine = None

    def tailor_resume(
        self,
        document_id: str,
        job_description: str,
        job_keywords: List[str],
        line_metadata: List[Dict[str, Any]],
        resume_text: str
    ) -> Dict[str, Any]:
        """
        Execute systematic two-phase tailoring.

        Returns final status and metrics.
        """
        print("\n" + "="*60)
        print("🚀 SYSTEMATIC RESUME TAILORING")
        print("="*60)

        # ============================================================
        # PHASE 1: VALIDATION
        # ============================================================

        phase1_results = self.validator.execute_phase_1(
            job_description,
            job_keywords,
            resume_text
        )

        if not phase1_results['should_proceed']:
            print("\n⚠️  WARNING: Low keyword match. Consider applying to different role.")
            print(f"   Only {phase1_results['match_percentage']:.1f}% of keywords are feasible.")
            # Optionally: return here or ask user to confirm

        # ============================================================
        # PHASE 2: SYSTEMATIC EDITING
        # ============================================================

        self.editor = SystematicEditor(self.docs_service, document_id)

        phase2_results = self.editor.execute_phase_2(
            line_metadata,
            phase1_results,
            space_borrowing_plan=None  # Will be generated if needed
        )

        # ============================================================
        # OVERFLOW RECOVERY (IF NEEDED)
        # ============================================================

        if phase2_results['requires_overflow_recovery']:
            print("\n⚠️  Page overflow detected. Running one-time recovery...")

            self.recovery_engine = OverflowRecoveryEngine(self.docs_service, document_id)
            recovery_results = self.recovery_engine.recover_from_overflow(
                line_metadata,
                phase1_results['feasible_keywords'],
                target_pages=1
            )

            if not recovery_results['success']:
                print("❌ Could not recover from overflow. Manual review needed.")

        # ============================================================
        # FINAL VALIDATION
        # ============================================================

        print("\n" + "="*60)
        print("✅ TAILORING COMPLETE")
        print("="*60)

        return {
            'phase1': phase1_results,
            'phase2': phase2_results,
            'final_ats_score': phase1_results['max_possible_ats_score'],
            'success': phase2_results['success']
        }
