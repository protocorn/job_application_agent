"""
Semantic Field Mapper

Maps form field labels to profile fields using local sentence embeddings
(all-MiniLM-L6-v2, ~80 MB).  Sits between the DB pattern lookup and the
Gemini API call in the filling pipeline:

    Deterministic  →  DB patterns  →  [Semantic]  →  Gemini

Why this helps
--------------
The DB lookup requires an exact (or fuzzy) string match, so "Legal Given Name"
won't match a DB entry for "First Name" even though they mean the same thing.
Sentence embeddings capture *meaning*, so any paraphrase of a field label
maps to the right profile field automatically.

Every successful semantic match is recorded back to field_label_patterns so
it becomes an exact DB match on the next run — the system gets faster over time.

Model
-----
all-MiniLM-L6-v2: 80 MB, 384-dim, ~5 ms per encode on CPU.
Loaded lazily on first use; model is cached for the process lifetime.
"""

import re
import os
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from loguru import logger


# --------------------------------------------------------------------------- #
#  Field anchor definitions                                                    #
# --------------------------------------------------------------------------- #

# For each profile field we list representative label phrasings.
# The more anchors the better — they form the "meaning cloud" for the field.
# When a new label comes in, its embedding is compared against all anchors and
# the field with the highest similarity wins (if it clears the threshold).

FIELD_ANCHORS: Dict[str, List[str]] = {
    "first_name": [
        "First name", "Given name", "Legal first name", "Forename",
        "First", "Your first name", "Applicant first name",
        "Legal given name", "Preferred first name", "Christian name",
        "Given names", "Legal given names",
    ],
    "last_name": [
        "Last name", "Surname", "Family name", "Legal last name",
        "Last", "Your last name", "Applicant last name",
        "Legal surname", "Legal family name", "Last/family name",
    ],
    "full_name": [
        "Full name", "Name", "Your name", "Applicant name",
        "Legal name", "Full legal name", "Complete name",
        "Candidate name",
    ],
    "email": [
        "Email", "Email address", "E-mail", "E-mail address",
        "Your email", "Contact email", "Work email", "Primary email",
    ],
    "phone": [
        "Phone", "Phone number", "Telephone", "Mobile",
        "Cell phone", "Contact number", "Mobile number",
        "Home phone", "Work phone", "Primary phone",
        "Home phone number", "Mobile phone number", "Phone/Mobile",
    ],
    "city": [
        "City", "Town", "Municipality", "City of residence",
        "Current city", "Where do you live", "Your city",
        "City/Town",
    ],
    "state": [
        "State", "Province", "Region", "State/Province",
        "State or province", "Your state", "Current state",
        "State/Territory",
    ],
    "country": [
        "Country", "Nation", "Country of residence",
        "Current country", "Your country", "Country/Region",
        "Where are you located", "Country of origin",
    ],
    "zip_code": [
        "Zip code", "Postal code", "ZIP", "Zip",
        "Post code", "Postcode", "ZIP/Postal code",
    ],
    "address": [
        "Address", "Street address", "Home address",
        "Address line 1", "Address line 2", "Street",
        "Mailing address", "Residential address", "Current address",
        "Permanent address",
    ],
    "linkedin": [
        "LinkedIn", "LinkedIn profile", "LinkedIn URL",
        "LinkedIn profile URL", "LinkedIn profile link",
        "LinkedIn profile address",
    ],
    "github": [
        "GitHub", "GitHub profile", "GitHub URL",
        "GitHub username", "GitHub profile link", "GitHub handle",
    ],
    "portfolio": [
        "Portfolio", "Portfolio URL", "Personal website",
        "Website", "Personal site", "Portfolio link",
        "Other website", "Personal webpage", "Personal URL",
        "Other links", "Additional links",
    ],
    "veteran_status": [
        "Veteran status", "Are you a veteran", "Military service",
        "Have you served in the military",
        "Protected veteran status", "Military veteran",
        "US veteran", "Armed forces",
    ],
    "disability_status": [
        "Disability status", "Do you have a disability",
        "Disability", "Self-identify disability",
        "Protected disability", "Disability disclosure",
        "Disability self-identification",
    ],
    "gender": [
        "Gender", "Gender identity", "Sex",
        "What is your gender", "What is your gender identity",
        "Gender/Sex",
    ],
    "race": [
        "Race", "Race/Ethnicity", "Racial identity",
        "Your race or ethnicity", "Racial background",
    ],
    "ethnicity": [
        "Ethnicity", "Ethnic background", "Ethnic identity",
        "Ethnic origin",
    ],
    "race_ethnicity": [
        "Race and ethnicity", "Race/Ethnicity", "Racial and ethnic identity",
        "Racial/ethnic background",
    ],
    "nationality": [
        "Nationality", "Country of citizenship",
        "Citizenship", "National origin", "Citizenship status",
    ],
    "visa_status": [
        "Work authorization", "Visa status", "Work visa",
        "Authorization to work", "Eligible to work in the US",
        "Are you authorized to work", "Authorized to work",
        "Employment eligibility",
    ],
    "sponsorship_required": [
        "Sponsorship required", "Do you require visa sponsorship",
        "Visa sponsorship", "Need sponsorship",
        "Require work visa sponsorship",
        "Will you now or in the future require sponsorship",
        "Require employer sponsorship",
    ],
    "willing_to_relocate": [
        "Willing to relocate", "Are you willing to relocate",
        "Relocation", "Open to relocation", "Would you relocate",
        "Prepared to relocate",
    ],
    "years_of_experience": [
        "Years of experience", "How many years of experience",
        "Experience level", "Total years of experience",
        "Total experience", "Professional experience",
    ],
    "current_title": [
        "Current title", "Job title", "Current position",
        "Current role", "Your title", "Position title",
        "Current job title", "Present title",
    ],
    "current_company": [
        "Current company", "Current employer", "Present company",
        "Where do you work", "Current organization",
        "Employer name", "Current workplace",
    ],
    "highest_education": [
        "Highest education", "Education level",
        "Highest degree", "Level of education",
        "Educational background", "Highest level of education",
        "Highest degree obtained", "Highest academic qualification",
    ],
    "degree": [
        "Degree", "Academic degree", "Degree type",
        "What degree do you have", "Your degree",
        "Field of degree", "Degree earned",
    ],
    "major": [
        "Major", "Field of study", "Area of study",
        "Academic major", "Course of study", "Concentration",
        "What is your major", "Undergraduate major",
    ],
    "gpa": [
        "GPA", "Grade point average", "Academic GPA",
        "Cumulative GPA", "Your GPA", "Overall GPA",
    ],
    "cover_letter": [
        "Cover letter", "Cover letter text",
        "Why do you want to work here",
        "Why are you interested in this role",
        "Additional comments", "Tell us about yourself",
        "Message to the hiring team",
    ],
    "start_date": [
        "Start date", "Available start date", "Earliest start date",
        "When can you start", "Availability", "Available from",
    ],
    "notice_period": [
        "Notice period", "How much notice do you need to give",
        "Current notice period", "Weeks notice",
        "When can you join", "Joining period",
    ],
    "salary_range": [
        "Salary", "Salary expectation", "Expected salary",
        "Desired compensation", "Compensation expectation",
        "Salary range", "What is your salary expectation",
    ],
    "preferred_name": [
        "Preferred name", "Preferred first name",
        "What do you go by", "Name you prefer to be called",
        "Nickname",
    ],
}


# --------------------------------------------------------------------------- #
#  Result dataclass                                                            #
# --------------------------------------------------------------------------- #

@dataclass
class SemanticMatch:
    profile_field: str
    confidence: float      # cosine similarity score (0-1)
    matched_anchor: str    # which anchor triggered the match (for debugging)


# --------------------------------------------------------------------------- #
#  Mapper                                                                      #
# --------------------------------------------------------------------------- #

class SemanticFieldMapper:
    """
    Lazy-loaded semantic field mapper.

    The model and embeddings are built on first use and cached for the
    lifetime of the process.  Subsequent calls are ~5 ms each.
    """

    # Minimum cosine similarity to accept a match.
    # all-MiniLM-L6-v2 cosine scores:
    #   "first name" ↔ "given name"        ≈ 0.85  (same concept)
    #   "first name" ↔ "forename"           ≈ 0.78
    #   "first name" ↔ "last name"          ≈ 0.70  (structurally similar, different meaning)
    #   "first name" ↔ "veteran status"     ≈ 0.25  (unrelated)
    # 0.72 sits just above the first_name/last_name ambiguity zone.
    SIMILARITY_THRESHOLD = 0.72

    # Confidence levels for downstream use
    HIGH_CONFIDENCE   = 0.85
    MEDIUM_CONFIDENCE = 0.72

    # Singleton: model and embeddings are shared across all instances
    _model           = None
    _anchor_matrix   = None   # (total_anchors, 384)
    _anchor_fields   = None   # parallel list of profile_field names
    _anchor_texts    = None   # parallel list of anchor text (for debug)
    _initialized     = False
    _init_failed     = False

    def __init__(self, extra_anchors: Optional[Dict[str, List[str]]] = None):
        """
        Args:
            extra_anchors: Additional {profile_field: [label_text, ...]} pairs
                           loaded from the DB at startup to extend static anchors.
        """
        self._extra_anchors = extra_anchors or {}

    # ---------------------------------------------------------------------- #
    #  Public API                                                              #
    # ---------------------------------------------------------------------- #

    def map_field(self, field_label: str) -> Optional[SemanticMatch]:
        """
        Map a field label to a profile field via semantic similarity.

        Returns SemanticMatch if similarity >= SIMILARITY_THRESHOLD, else None.
        """
        if not self._ensure_initialized():
            return None

        try:
            label_vec = self._model.encode(
                [field_label],
                normalize_embeddings=True,
                show_progress_bar=False,
            )[0]  # shape: (384,)

            # Cosine similarity against all anchors (vectors already normalized)
            sims = self._anchor_matrix @ label_vec  # shape: (N,)

            best_idx  = int(np.argmax(sims))
            best_sim  = float(sims[best_idx])
            best_field = self._anchor_fields[best_idx]
            best_anchor = self._anchor_texts[best_idx]

            if best_sim < self.SIMILARITY_THRESHOLD:
                logger.debug(
                    f"SemanticFieldMapper: No confident match for '{field_label}' "
                    f"(best: '{best_field}' @ {best_sim:.2f})"
                )
                return None

            logger.info(
                f"SemanticFieldMapper: '{field_label}' → '{best_field}' "
                f"(similarity={best_sim:.2f}, anchor='{best_anchor}')"
            )
            return SemanticMatch(
                profile_field=best_field,
                confidence=best_sim,
                matched_anchor=best_anchor,
            )

        except Exception as e:
            logger.error(f"SemanticFieldMapper: map_field failed: {e}")
            return None

    def add_db_anchors(self, db_patterns: Dict[str, List[str]]):
        """
        Extend the anchor index with successful DB patterns loaded at startup.

        Call this after loading field_label_patterns from the DB to give the
        model all the real-world labels it has already seen.

        Args:
            db_patterns: {profile_field: [field_label_raw, ...]}
        """
        if not db_patterns:
            return
        self._extra_anchors.update(db_patterns)
        # Force re-init on next use
        SemanticFieldMapper._initialized = False
        SemanticFieldMapper._init_failed  = False
        logger.info(
            f"SemanticFieldMapper: Added DB anchors for "
            f"{len(db_patterns)} fields — will re-index on next call."
        )

    # ---------------------------------------------------------------------- #
    #  Lazy initialization                                                     #
    # ---------------------------------------------------------------------- #

    def _ensure_initialized(self) -> bool:
        if SemanticFieldMapper._initialized:
            return True
        if SemanticFieldMapper._init_failed:
            return False

        try:
            from sentence_transformers import SentenceTransformer

            logger.info("SemanticFieldMapper: Loading all-MiniLM-L6-v2 model...")
            SemanticFieldMapper._model = SentenceTransformer(
                "all-MiniLM-L6-v2",
                cache_folder=os.path.join(
                    os.path.dirname(__file__), "..", "..", ".model_cache"
                ),
            )
            logger.info("SemanticFieldMapper: Model loaded.")

            self._build_index()
            SemanticFieldMapper._initialized = True
            return True

        except ImportError:
            logger.error(
                "SemanticFieldMapper: sentence-transformers not installed. "
                "Run: pip install sentence-transformers"
            )
            SemanticFieldMapper._init_failed = True
            return False

        except Exception as e:
            logger.error(f"SemanticFieldMapper: Initialization failed: {e}")
            SemanticFieldMapper._init_failed = True
            return False

    def _build_index(self):
        """Build the (N, 384) anchor matrix from static + dynamic anchors."""
        # Merge static anchors with any extra ones (DB-loaded or test)
        merged: Dict[str, List[str]] = {}
        for field, anchors in FIELD_ANCHORS.items():
            merged[field] = list(anchors)
        for field, anchors in self._extra_anchors.items():
            if field in merged:
                # De-duplicate
                existing = set(a.lower() for a in merged[field])
                merged[field] += [a for a in anchors if a.lower() not in existing]
            else:
                merged[field] = list(anchors)

        # Flatten to parallel lists
        fields_list  = []
        texts_list   = []
        for field, anchors in merged.items():
            for anchor in anchors:
                fields_list.append(field)
                texts_list.append(anchor)

        logger.info(
            f"SemanticFieldMapper: Encoding {len(texts_list)} anchors "
            f"for {len(merged)} profile fields..."
        )

        embeddings = SemanticFieldMapper._model.encode(
            texts_list,
            normalize_embeddings=True,
            batch_size=64,
            show_progress_bar=False,
        )  # shape: (N, 384)

        SemanticFieldMapper._anchor_matrix = embeddings
        SemanticFieldMapper._anchor_fields = fields_list
        SemanticFieldMapper._anchor_texts  = texts_list

        logger.info("SemanticFieldMapper: Index built.")

    # ---------------------------------------------------------------------- #
    #  Utility                                                                 #
    # ---------------------------------------------------------------------- #

    @staticmethod
    def is_available() -> bool:
        """Check if sentence-transformers is installed without loading the model."""
        try:
            import sentence_transformers  # noqa: F401
            return True
        except ImportError:
            return False
