"""
Complete Systematic Resume Tailoring Implementation
Full two-phase approach with all methods implemented
"""

import re
import json
import math
from typing import Dict, List, Any, Tuple, Optional
from google import genai
from google.api_core import retry
import os
from gemini_rate_limiter import generate_content_with_retry


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def extract_relevant_mimikree_context(mimikree_data: str, keywords: List[str], max_length: int = 800) -> str:
    """
    Intelligently extract relevant portions of Mimikree data based on keywords.

    Instead of blindly slicing, find paragraphs/sentences that mention the keywords.

    Args:
        mimikree_data: Full Mimikree response data
        keywords: List of keywords to look for
        max_length: Maximum character length to return

    Returns:
        Most relevant excerpts from Mimikree data
    """
    if not mimikree_data or not isinstance(mimikree_data, str):
        return ""

    if not keywords:
        return mimikree_data[:max_length]

    # Split into paragraphs (by double newline) or sentences
    paragraphs = re.split(r'\n\n+', mimikree_data)

    # Score each paragraph by keyword matches
    scored_paragraphs = []
    for para in paragraphs:
        if not para.strip():
            continue

        score = 0
        para_lower = para.lower()

        # Count keyword matches
        for keyword in keywords:
            keyword_lower = keyword.lower()
            # Full keyword match
            if keyword_lower in para_lower:
                score += 10
            # Partial word matches
            keyword_words = keyword_lower.split()
            for word in keyword_words:
                if word in para_lower:
                    score += 2

        if score > 0:
            scored_paragraphs.append((score, para))

    # Sort by score (highest first)
    scored_paragraphs.sort(reverse=True, key=lambda x: x[0])

    # Combine top paragraphs until we reach max_length
    result = []
    total_length = 0

    for score, para in scored_paragraphs:
        if total_length + len(para) + 2 <= max_length:  # +2 for newline
            result.append(para)
            total_length += len(para) + 2
        elif total_length < max_length:
            # Add truncated version of last paragraph
            remaining = max_length - total_length
            result.append(para[:remaining] + "...")
            break

    return "\n\n".join(result) if result else mimikree_data[:max_length]


def clean_gemini_response(text: str) -> str:
    """
    Clean up artifacts from Gemini responses.
    
    Gemini sometimes adds metadata like:
    - "Condensed Bullet (88 chars):"
    - "Here is the rewritten text:"
    - "(31 chars)" at the end
    - "Result:", "Output:", etc.
    
    This function removes such artifacts and returns only the actual content.
    """
    import re
    
    text = text.strip()
    
    # Remove character count patterns like "(31 chars)", "(88 chars)", etc.
    text = re.sub(r'\s*\(\d+\s*chars?\)\.?\s*$', '', text, flags=re.IGNORECASE)
    
    # Remove word count patterns like "(15 words)"
    text = re.sub(r'\s*\(\d+\s*words?\)\.?\s*$', '', text, flags=re.IGNORECASE)
    
    # Split into lines
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        line = line.strip()
        
        # Skip empty lines
        if not line:
            continue
        
        # Remove char count from end of line if present
        line = re.sub(r'\s*\(\d+\s*chars?\)\.?\s*$', '', line, flags=re.IGNORECASE)
        
        # Skip lines that are clearly metadata/artifacts
        skip_patterns = [
            'Condensed',
            'Here is',
            'Here\'s',
            'Result:',
            'Output:',
            'Rewritten:',
            'New text:',
            'Updated:',
            'Modified:',
            'Final:',
            'Answer:',
        ]
        
        # Check if line starts with any skip pattern
        should_skip = any(line.startswith(pattern) for pattern in skip_patterns)
        
        # Also skip short lines ending with colon (likely labels)
        if line.endswith(':') and len(line) < 40:
            should_skip = True
        
        if not should_skip and line:
            cleaned_lines.append(line)
    
    # If we have cleaned lines, use them; otherwise use original
    if cleaned_lines:
        # If single line, return it
        if len(cleaned_lines) == 1:
            result = cleaned_lines[0]
        else:
            # If multiple lines, join with space (for bullet points)
            result = ' '.join(cleaned_lines)
        
        # Final cleanup - remove any remaining char counts
        result = re.sub(r'\s*\(\d+\s*chars?\)\.?\s*$', '', result, flags=re.IGNORECASE)
        return result.strip()
    else:
        # Fallback to original if cleaning removed everything
        return text


# ============================================================
# PHASE 1: KEYWORD VALIDATION IMPLEMENTATION
# ============================================================

class KeywordValidatorComplete:
    """Complete implementation of keyword validation with Mimikree."""

    def __init__(self, mimikree_responses: Dict[str, str]):
        """
        Args:
            mimikree_responses: Dict of question -> response from Mimikree
        """
        self.mimikree_responses = mimikree_responses

        # Initialize Gemini client for keyword analysis
        api_key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY environment variable required")
        self.genai_client = genai.Client(api_key=api_key)

    def execute_phase_1(
        self,
        job_keywords: List[str],
        resume_text: str
    ) -> Dict[str, Any]:
        """
        Phase 1: Validate which keywords user actually has experience with.

        Returns:
            {
                'feasible_keywords': [...],
                'missing_keywords': [...],
                'keyword_evidence': {...},
                'max_possible_ats_score': float,
                'should_proceed': bool,
                'match_percentage': float
            }
        """
        print("\n" + "="*60)
        print("PHASE 1: KEYWORD VALIDATION & SKILL FEASIBILITY")
        print("="*60)

        # Analyze Mimikree responses for each keyword
        print(f"\n📋 Analyzing {len(job_keywords)} keywords from Mimikree responses...")

        keyword_evidence = {}
        feasible = []
        missing = []

        # Prepare Mimikree context for Gemini analysis
        mimikree_context = "\n\n".join([
            f"Q: {question}\nA: {response}"
            for question, response in self.mimikree_responses.items()
        ])

        # Use Gemini to intelligently analyze keywords in batches
        print(f"   🤖 Using Gemini to analyze keyword evidence...")
        batch_size = 10  # Process all keywords at once for consistency

        for i in range(0, len(job_keywords), batch_size):
            batch = job_keywords[i:i+batch_size]

            prompt = f"""You are an expert HR analyst evaluating whether a candidate has genuine experience with specific skills/keywords.

**Candidate's Professional Profile:**
{mimikree_context}

**Keywords to Evaluate:**
{json.dumps(batch, indent=2)}

**Your Task:**
For EACH keyword, determine if the candidate has demonstrable experience based on the profile above.

**Critical Guidelines:**
1. Consider ALL synonyms and related terms:
   - "NLP" includes "Natural Language Processing", "text analysis", "language models"
   - "AI" includes "Artificial Intelligence", "machine learning models", "intelligent systems"
   - "Generative AI" includes "LLMs", "GPT", "transformers", "generative models"
   - "Computer Vision" includes "image processing", "visual recognition", "CV"
   - "Data Analysis" includes "analytics", "statistical analysis", "data exploration"

2. Look for concrete evidence:
   - Specific projects, technologies, or tools mentioned
   - Metrics or outcomes (e.g., "improved accuracy by 20%")
   - Detailed technical descriptions

3. Be GENEROUS but HONEST:
   - If they mention a related concept with substance, count it as evidence
   - High confidence = detailed projects with metrics
   - Medium confidence = keyword/synonym mentioned with some context
   - Low confidence = vague or minimal mention

**Output Format (valid JSON only):**
{{
  "Keyword Name": {{
    "has_evidence": true/false,
    "confidence": "high"/"medium"/"low",
    "reasoning": "1-2 sentence explanation"
  }}
}}

Return ONLY valid JSON, no markdown formatting, no extra text."""

            try:
                response = generate_content_with_retry(
                    client=self.genai_client,
                    model='gemini-2.0-flash-exp',
                    contents=prompt
                )

                # Clean and parse Gemini's response
                # (json and re already imported at module level)

                # Remove markdown code blocks if present
                cleaned_text = response.text.strip()
                cleaned_text = re.sub(r'```json\s*', '', cleaned_text)
                cleaned_text = re.sub(r'```\s*', '', cleaned_text)

                # Extract JSON
                json_match = re.search(r'\{.*\}', cleaned_text, re.DOTALL)
                if json_match:
                    analysis = json.loads(json_match.group())

                    # Process each keyword in the batch
                    for keyword in batch:
                        # Try exact match first, then case-insensitive
                        keyword_data = analysis.get(keyword)
                        if not keyword_data:
                            # Try case-insensitive match
                            for k, v in analysis.items():
                                if k.lower() == keyword.lower():
                                    keyword_data = v
                                    break

                        if keyword_data:
                            has_evidence = keyword_data.get('has_evidence', False)
                            confidence = keyword_data.get('confidence', 'low')
                            reasoning = keyword_data.get('reasoning', '')

                            keyword_evidence[keyword] = {
                                'has_experience': has_evidence,
                                'evidence': reasoning,
                                'confidence': confidence
                            }

                            if has_evidence:
                                feasible.append(keyword)
                                print(f"   ✓ {keyword}: Evidence found ({confidence} confidence)")
                            else:
                                missing.append(keyword)
                                print(f"   ✗ {keyword}: No evidence in Mimikree responses")
                        else:
                            # Keyword not in Gemini response
                            missing.append(keyword)
                            print(f"   ✗ {keyword}: Not analyzed by Gemini")
                else:
                    # Fallback if JSON parsing fails
                    print(f"   ⚠️ Failed to parse Gemini response for batch")
                    for keyword in batch:
                        missing.append(keyword)
                        keyword_evidence[keyword] = {
                            'has_experience': False,
                            'evidence': 'Analysis failed',
                            'confidence': 'low'
                        }

            except Exception as e:
                print(f"   ⚠️ Error analyzing keywords with Gemini: {e}")
                for keyword in batch:
                    missing.append(keyword)
                    keyword_evidence[keyword] = {
                        'has_experience': False,
                        'evidence': f'Error: {str(e)}',
                        'confidence': 'low'
                    }

        # Calculate max possible ATS score
        max_ats_score = 100
        max_ats_score -= len(missing) * 15  # 15 points per missing keyword

        # Check keyword placement
        if len(feasible) < 3:
            max_ats_score -= 10  # Poor keyword placement penalty

        max_ats_score = max(0, max_ats_score)

        # Decide if tailoring is worthwhile
        match_percentage = (len(feasible) / len(job_keywords) * 100) if job_keywords else 0
        should_proceed = match_percentage >= 40  # At least 40% match

        print(f"\n📊 FEASIBILITY ANALYSIS:")
        print(f"   Feasible keywords: {len(feasible)}/{len(job_keywords)} ({match_percentage:.1f}%)")
        print(f"   Missing keywords: {len(missing)}/{len(job_keywords)}")
        print(f"   Max possible ATS score: {max_ats_score}/100")

        if should_proceed:
            print(f"   ✅ PROCEED - Good keyword match")
        else:
            print(f"   ⚠️  WARNING - Low keyword match (<40%)")
            print(f"   Consider applying to a different role more aligned with your experience")

        if missing:
            print(f"\n⚠️  Cannot add these {len(missing)} keywords (no experience found):")
            for kw in missing[:5]:  # Show first 5
                print(f"      • {kw}")
            if len(missing) > 5:
                print(f"      ... and {len(missing) - 5} more")

        return {
            'feasible_keywords': feasible,
            'missing_keywords': missing,
            'keyword_evidence': keyword_evidence,
            'max_possible_ats_score': max_ats_score,
            'should_proceed': should_proceed,
            'match_percentage': match_percentage
        }


# ============================================================
# PHASE 2: SYSTEMATIC EDITING IMPLEMENTATION
# ============================================================

class SystematicEditorComplete:
    """Complete implementation of systematic section-wise editing."""

    def __init__(self, genai_client):
        self.genai_client = genai_client

    def execute_phase_2(
        self,
        line_metadata: List[Dict[str, Any]],
        validation_results: Dict[str, Any],
        mimikree_data: str,
        job_description: str,
        conservative_mode: bool = True
    ) -> Dict[str, Any]:
        """
        Execute systematic section-wise editing.
        
        Args:
            conservative_mode: If True, only edits Profile → Skills → Projects (strategic changes only)
                             If False, edits everything including experience bullets (aggressive)

        Returns all edits to be applied in one batch.
        """
        print("\n" + "="*60)
        if conservative_mode:
            print("PHASE 2: CONSERVATIVE STRATEGIC EDITING")
            print("Strategy: Profile → Skills → Projects ONLY")
        else:
            print("PHASE 2: SYSTEMATIC SECTION-WISE EDITING")
        print("="*60)

        # Step 1: Identify sections
        sections = self._identify_sections(line_metadata)
        print(f"\n📂 Identified {len(sections)} sections:")
        for section in sections:
            print(f"   • {section['name']} ({len(section['lines'])} lines)")

        # Step 2: Calculate space borrowing plan
        from space_borrowing import calculate_relevance_scores, identify_space_borrowing_opportunities

        scored_lines = calculate_relevance_scores(
            line_metadata,
            validation_results['feasible_keywords'],
            job_description
        )

        space_plan = identify_space_borrowing_opportunities(
            scored_lines,
            validation_results['feasible_keywords']
        )

        # Step 3: Process each section (priority-based in conservative mode)
        all_replacements = []
        section_line_changes = {}  # Track line changes per section
        total_lines_added = 0
        
        # Define processing order and what to skip
        if conservative_mode:
            # Conservative: Only Profile → Skills → Projects
            priority_order = ['profile', 'skills', 'projects']
            skip_types = ['experience', 'education', 'achievements', 'publications']
        else:
            # Aggressive: Process everything
            priority_order = ['profile', 'experience', 'projects', 'skills']
            skip_types = []
        
        # Sort sections by priority
        prioritized_sections = []
        for section_type in priority_order:
            for section in sections:
                if section['type'] == section_type:
                    prioritized_sections.append(section)
        
        # Add remaining sections if not in conservative mode
        if not conservative_mode:
            for section in sections:
                if section not in prioritized_sections:
                    prioritized_sections.append(section)

        for section in prioritized_sections:
            section_type = section['type']
            
            # Skip sections based on mode
            if section_type in skip_types:
                print(f"\n⏭️  Skipping section: {section['name']} (conservative mode)")
                continue
                
            print(f"\n🔧 Processing section: {section['name']} (Priority {priority_order.index(section_type) + 1 if section_type in priority_order else 'N/A'})")
            
            # Count lines BEFORE editing (only non-empty lines with actual content)
            lines_before = sum(1 for line in section['lines'] if line.get('text', '').strip())

            if section_type == 'profile':
                replacements = self._edit_profile_section(
                    section, validation_results, mimikree_data, conservative_mode
                )
            elif section_type == 'experience':
                replacements = self._edit_experience_section(
                    section, validation_results, mimikree_data, space_plan
                )
            elif section_type == 'projects':
                replacements = self._edit_projects_section(
                    section, validation_results, mimikree_data, space_plan, conservative_mode
                )
            elif section_type == 'skills':
                replacements = self._edit_skills_section(
                    section, validation_results, conservative_mode
                )
            else:
                replacements = []

            # Estimate lines AFTER editing (based on replacements)
            # This is an approximation since actual line count depends on rendering
            # Only count non-empty lines (lines with actual content)
            lines_after = lines_before
            for repl in replacements:
                old_text = repl.get('old_text', '')
                new_text = repl.get('new_text', '')
                
                # Count only non-empty lines (lines with text content)
                old_line_count = sum(1 for line in old_text.split('\n') if line.strip())
                new_line_count = sum(1 for line in new_text.split('\n') if line.strip())
                
                lines_after += (new_line_count - old_line_count)
            
            lines_delta = lines_after - lines_before
            section_line_changes[section['name']] = {
                'before': lines_before,
                'after': lines_after,
                'delta': lines_delta
            }
            total_lines_added += lines_delta

            all_replacements.extend(replacements)
            print(f"   ✓ Generated {len(replacements)} replacement(s)")
            if lines_delta != 0:
                print(f"   📏 Estimated lines: {lines_before} → {lines_after} ({lines_delta:+d})")

        # Print summary of line changes
        if section_line_changes:
            print(f"\n📊 Total estimated lines added across all sections: {total_lines_added:+d}")

        return {
            'replacements': all_replacements,
            'sections_modified': [s['name'] for s in sections if s['lines']],
            'space_plan': space_plan,
            'section_line_changes': section_line_changes,
            'total_lines_added': total_lines_added
        }

    def _identify_sections(self, line_metadata: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Identify resume sections."""
        sections = []
        current_section = None

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

            # Check if section header
            is_header = False
            section_type = 'other'

            for stype, pattern in SECTION_PATTERNS.items():
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

    def _edit_profile_section(
        self,
        section: Dict[str, Any],
        validation: Dict[str, Any],
        mimikree_data: str,
        conservative_mode: bool = True
    ) -> List[Dict[str, Any]]:
        """Edit profile/summary section with feasible keywords.
        
        In conservative mode: Only rewrites if can add 3+ missing keywords naturally.
        """
        replacements = []

        if not section['lines']:
            return replacements

        # Get profile text
        profile_lines = [line['text'] for line in section['lines']]
        original_profile = ' '.join(profile_lines)
        
        # In conservative mode, check if profile already has keywords
        if conservative_mode:
            profile_lower = original_profile.lower()
            keywords_present = sum(1 for kw in validation['feasible_keywords'][:10] 
                                  if kw.lower() in profile_lower)
            keywords_missing = len(validation['feasible_keywords'][:10]) - keywords_present
            
            if keywords_missing < 3:
                print(f"      ℹ️  Profile already has {keywords_present}/10 top keywords - skipping rewrite")
                return replacements
            else:
                print(f"      📝 Profile missing {keywords_missing} keywords - will rewrite")

        # Use Gemini to rewrite with feasible keywords
        # Intelligently extract relevant Mimikree context
        mimikree_context = extract_relevant_mimikree_context(
            mimikree_data,
            validation['feasible_keywords'][:7],
            max_length=500
        )

        prompt = f"""You are a professional resume writer. Rewrite this resume profile to sound natural, engaging, and professional while incorporating SPECIFIC skills (not vague terms).

ORIGINAL PROFILE:
{original_profile}

TARGET SKILLS/KEYWORDS (incorporate ONLY if you can be specific):
{', '.join(validation['feasible_keywords'][:7])}

{f"CANDIDATE'S BACKGROUND (use for context):\\n{mimikree_context}\\n" if mimikree_context else ""}

CRITICAL RULES - PROFESSIONALISM FIRST:
1. **BE SPECIFIC, NOT VAGUE**: Use concrete terms. "Natural Language Processing" > "Machine Learning". "TensorFlow" > "AI". "Python data pipelines" > "programming".
2. **ONLY USE BROAD TERMS AS LAST RESORT**: If a keyword is vague (e.g., "Machine Learning", "AI", "Data Science"), only include it if:
   - There's NO specific alternative available
   - You can tie it to a concrete achievement (e.g., "Machine Learning for medical diagnosis")
   - It's absolutely critical for the role
3. **NATURAL FLOW**: Write like a human, not a robot. Tell a story, don't list keywords.
4. **NO KEYWORD STUFFING**: Never list keywords like "proficient in X, Y, and Z" or "skilled in A, B, C"
5. **SHOW, DON'T TELL**: Instead of saying "skilled in Machine Learning", say "built ML-powered search systems"
6. **SAME LENGTH**: Keep similar length to original (±10%)

GOOD EXAMPLES (specific, professional):
❌ BAD: "AI/ML Engineer skilled in Machine Learning, Python, and Data Analysis"
✅ GOOD: "AI Engineer building retrieval-augmented generation systems with vector databases"

❌ BAD: "Data Scientist with expertise in AI and Machine Learning"
✅ GOOD: "Data Scientist developing production NLP models for medical document analysis"

❌ BAD: "leveraging Machine Learning and Python to deliver AI-powered systems"
✅ GOOD: "designing search systems using semantic embeddings and transformer models"

VAGUE TERMS TO AVOID (unless no alternative):
- "Machine Learning" (use: NLP, Computer Vision, Deep Learning, etc.)
- "AI" (use: LLMs, Neural Networks, Transformers, etc.)
- "Data Science" (use: Statistical Analysis, Predictive Modeling, etc.)
- "Programming" (use: Python, TypeScript, etc.)

WHAT TO AVOID:
- Don't use "leveraging", "proficient in", "skilled in", "expertise in"
- Don't list technologies in comma-separated lists
- Don't use generic phrases like "deliver solutions" or "drive results"
- Don't force vague keywords just to include them

OUTPUT:
Return ONLY the rewritten profile text. No explanations, no metadata, no commentary."""

        try:
            response = generate_content_with_retry(
                client=self.genai_client,
                model='gemini-2.0-flash-exp',
                contents=prompt
            )

            new_profile = clean_gemini_response(response.text)

            # Create replacement for each line
            # For simplicity, replace the first substantial line
            for line in section['lines']:
                if len(line['text']) > 30:  # First substantial line
                    replacements.append({
                        'old_text': line['text'],
                        'new_text': new_profile,
                        'type': 'profile_rewrite'
                    })
                    break  # Only replace first line for now

        except Exception as e:
            # Re-raise rate limit errors to allow retry mechanism to work
            error_str = str(e)
            if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str or 'quota' in error_str.lower():
                raise
            print(f"      ⚠️  Error generating profile rewrite: {e}")

        return replacements

    def _edit_experience_section(
        self,
        section: Dict[str, Any],
        validation: Dict[str, Any],
        mimikree_data: str,
        space_plan: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Edit experience section with space borrowing."""
        replacements = []

        # Get bullets from experience
        bullets = [line for line in section['lines'] if line.get('bullet_level', 0) > 0]

        if not bullets:
            return replacements

        # Apply space borrowing logic
        donors = space_plan.get('donor_lines', [])
        receivers = space_plan.get('receiver_lines', [])

        # Identify which bullets to condense and which to expand
        condense_targets = []
        expand_targets = []

        for donor in donors[:3]:  # Top 3 donors
            for bullet in bullets:
                if bullet['line_number'] == donor['line_number']:
                    condense_targets.append(bullet)
                    break

        for receiver in receivers[:3]:  # Top 3 receivers
            for bullet in bullets:
                if bullet['line_number'] == receiver['line_number']:
                    expand_targets.append(bullet)
                    break

        # Generate replacements for condensed bullets
        for bullet in condense_targets:
            # Fix off-by-one: stored value is actual_lines + 1
            stored_visual_lines = bullet.get('visual_lines', 2)
            current_visual_lines = max(1, stored_visual_lines - 1)  # Actual visual lines

            # CRITICAL: NEVER condense to 1 line
            # We can only condense if current > 2 lines (so target would be >= 2 lines)
            # If already 2 lines or less, skip it - either expand it or remove it completely
            if current_visual_lines <= 2:
                print(f"         ⏭️  Skipping ({current_visual_lines} lines - NEVER condense to 1 line): {bullet['text'][:40]}...")
                continue

            # Target is current - 1, but never less than 2 lines
            target_visual_lines = max(2, current_visual_lines - 1)
            chars_per_line = bullet.get('char_limit', 80)
            target_chars = target_visual_lines * chars_per_line

            prompt = f"""Condense this resume bullet point to save visual space:

ORIGINAL ({len(bullet['text'])} chars, {current_visual_lines} lines):
{bullet['text']}

GOAL: Reduce to {target_visual_lines} line(s) or fewer (~{target_chars} chars max)

CRITICAL RULE: NEVER condense to 1 line. Minimum target is 2 lines.

REQUIREMENTS:
- Remove unnecessary words: "various", "effectively", "successfully", etc.
- Keep ALL numbers and percentages
- Keep core accomplishment
- Must fit in {target_visual_lines} visual line(s) to save actual space
- NEVER reduce below 2 lines

WHY: Single-line bullets look unnatural and are hard to read. We strictly never condense to 1 line.
If a bullet is currently 2 lines or less, we either expand it or remove it completely - but NEVER condense it.

CRITICAL OUTPUT FORMAT:
- Return ONLY the condensed bullet text
- NO metadata, character counts, or labels
- Just the clean text for the resume

If cannot be condensed to {target_visual_lines} line(s) while keeping key info, return original."""

            try:
                response = generate_content_with_retry(
                    client=self.genai_client,
                    model='gemini-2.0-flash-exp',
                    contents=prompt
                )

                condensed = clean_gemini_response(response.text)
                
                # Verify we actually saved a visual line
                condensed_visual_lines = int((len(condensed) / chars_per_line) + 0.9)
                
                if condensed_visual_lines < current_visual_lines:
                    replacements.append({
                        'old_text': bullet['text'],
                        'new_text': condensed,
                        'type': 'condense',
                        'reason': 'space_borrowing_donor'
                    })
                    print(f"         ✓ Condensed ({current_visual_lines}→{condensed_visual_lines} lines): {condensed[:40]}...")
                else:
                    print(f"         ⏭️  No visual line saved: {bullet['text'][:40]}...")
                    
            except Exception as e:
                # Re-raise rate limit errors to allow retry mechanism to work
                error_str = str(e)
                if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str or 'quota' in error_str.lower():
                    raise
                print(f"      ⚠️  Error condensing bullet: {e}")

        # Generate replacements for expanded bullets
        for bullet in expand_targets:
            char_available = bullet.get('char_buffer', 0)

            if char_available < 20:
                continue  # Not enough space to expand meaningfully

            # Intelligently extract relevant Mimikree context for this bullet
            # Look for paragraphs mentioning the keywords we're trying to add
            mimikree_context = extract_relevant_mimikree_context(
                mimikree_data,
                validation['feasible_keywords'][:5],
                max_length=800
            )

            prompt = f"""Expand this resume bullet with more specific details{' from the background info' if mimikree_context else ''}:

ORIGINAL BULLET:
{bullet['text']}

{f"BACKGROUND INFO:\\n{mimikree_context}\\n" if mimikree_context else ""}
REQUIREMENTS:
- Add specific methodology, tools, or impact details
- Incorporate these keywords if relevant: {', '.join(validation['feasible_keywords'][:5])}
- Keep all original quantified data
- Add up to {char_available} more characters
- Be specific and truthful

CRITICAL OUTPUT FORMAT:
- Return ONLY the expanded bullet text
- NO metadata, character counts, or explanatory text
- Just the clean bullet text that goes directly into the resume"""

            try:
                response = generate_content_with_retry(
                    client=self.genai_client,
                    model='gemini-2.0-flash-exp',
                    contents=prompt
                )

                expanded = clean_gemini_response(response.text)

                # Validate expansion doesn't exceed char_buffer
                added_chars = len(expanded) - len(bullet['text'])
                if added_chars <= char_available + 10:  # 10 char tolerance
                    replacements.append({
                        'old_text': bullet['text'],
                        'new_text': expanded,
                        'type': 'expand',
                        'reason': 'space_borrowing_receiver'
                    })
            except Exception as e:
                # Re-raise rate limit errors to allow retry mechanism to work
                error_str = str(e)
                if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str or 'quota' in error_str.lower():
                    raise
                print(f"      ⚠️  Error expanding bullet: {e}")

        return replacements

    def _edit_projects_section(
        self,
        section: Dict[str, Any],
        validation: Dict[str, Any],
        mimikree_data: str,
        space_plan: Dict[str, Any],
        conservative_mode: bool = True
    ) -> List[Dict[str, Any]]:
        """Edit projects section.
        
        In conservative mode: Only identifies low-relevance projects for potential replacement.
                            Does NOT modify existing relevant projects.
        In aggressive mode: Enhances all project bullets with keywords.
        """
        replacements = []

        # Parse project structure
        projects = self._parse_projects(section['lines'])

        print(f"      Found {len(projects)} project(s)")

        # Calculate relevance for all projects
        projects_with_relevance = []
        for project in projects:
            relevance = self._calculate_project_relevance(
                project,
                validation['feasible_keywords']
            )
            projects_with_relevance.append((project, relevance))
            print(f"      • {project['title_text'][:50]}... (relevance: {relevance:.0f}/100)")

        if conservative_mode:
            # Conservative mode: Only flag low-relevance projects
            low_relevance_projects = [(p, r) for p, r in projects_with_relevance if r < 20]
            
            if low_relevance_projects:
                print(f"      ⚠️  Found {len(low_relevance_projects)} low-relevance project(s) (< 20/100)")
                print(f"      💡 Suggestion: Consider replacing with more relevant projects from your experience")
                # Note: Actual replacement would need new project content from Mimikree
                # For now, we just identify but don't replace
            else:
                print(f"      ✅ All projects are relevant (>= 20/100) - no changes needed")
            
            # In conservative mode, we don't modify project bullets
            return replacements

        # Aggressive mode: Enhance project bullets with keywords
        for project, relevance in projects_with_relevance:
            # For each project bullet, enhance with keywords
            for bullet in project['description_bullets']:
                # Check if bullet needs keyword injection
                has_keyword = any(
                    kw.lower() in bullet['text'].lower()
                    for kw in validation['feasible_keywords']
                )

                if not has_keyword and bullet.get('char_buffer', 0) > 15:
                    # Add keywords to this bullet
                    prompt = f"""Rewrite this project bullet to naturally include relevant keywords:

ORIGINAL:
{bullet['text']}

KEYWORDS TO INCORPORATE (if relevant): {', '.join(validation['feasible_keywords'][:5])}

REQUIREMENTS:
- Keep the same core accomplishment
- Add keywords naturally where they fit
- Keep all quantified data
- Stay within {bullet.get('char_buffer', 20)} additional characters
- Be truthful - only add keywords that make sense

CRITICAL OUTPUT FORMAT:
- Return ONLY the rewritten bullet text
- NO metadata or character counts
- Just the clean text for the resume"""

                    try:
                        response = generate_content_with_retry(
                            client=self.genai_client,
                            model='gemini-2.0-flash-exp',
                            contents=prompt
                        )

                        rewritten = clean_gemini_response(response.text)

                        # Validate length
                        added = len(rewritten) - len(bullet['text'])
                        if added <= bullet.get('char_buffer', 0) + 10:
                            replacements.append({
                                'old_text': bullet['text'],
                                'new_text': rewritten,
                                'type': 'project_bullet_enhance'
                            })
                    except Exception as e:
                        # Re-raise rate limit errors to allow retry mechanism to work
                        error_str = str(e)
                        if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str or 'quota' in error_str.lower():
                            raise
                        print(f"         ⚠️  Error enhancing bullet: {e}")

        return replacements

    def _edit_skills_section(
        self,
        section: Dict[str, Any],
        validation: Dict[str, Any],
        conservative_mode: bool = True
    ) -> List[Dict[str, Any]]:
        """Edit skills section to highlight feasible keywords.
        
        In conservative mode: Only reorganizes (no additions), prioritizes job-relevant skills first.
        """
        replacements = []

        if not section['lines']:
            return replacements

        # Skills are usually in one or two lines
        skills_text = ' '.join([line['text'] for line in section['lines']])
        
        # In conservative mode, check if top keywords are already prioritized
        if conservative_mode:
            skills_lower = skills_text.lower()
            first_100_chars = skills_lower[:100]
            top_keywords_present = sum(1 for kw in validation['feasible_keywords'][:5] 
                                       if kw.lower() in first_100_chars)
            
            if top_keywords_present >= 3:
                print(f"      ℹ️  Skills already well-organized ({top_keywords_present}/5 top keywords at front) - skipping")
                return replacements
            else:
                print(f"      📝 Reorganizing skills to prioritize job keywords")

        # Use Gemini to reorganize skills to prioritize feasible keywords
        prompt = f"""Reorganize this skills section to highlight SPECIFIC skills from the priority list. NEVER add vague terms.

ORIGINAL SKILLS:
{skills_text}

PRIORITY SKILLS (only reorganize to highlight SPECIFIC ones that exist):
{', '.join(validation['feasible_keywords'][:10])}

STRICT REQUIREMENTS - NO EXCEPTIONS:
1. **NEVER ADD NEW SKILLS** - Only reorganize existing skills
2. **NEVER ADD VAGUE TERMS** - Don't add broad terms like "Machine Learning", "AI", "Data Science" unless they already exist in original
3. **PRIORITIZE SPECIFIC SKILLS** - Move specific tools/technologies to front (e.g., "TensorFlow", "PyTorch", "LangChain")
4. **KEEP ALL ORIGINAL SKILLS** - Don't remove anything
5. **SAME FORMAT** - Maintain exact format (comma-separated or with pipes)
6. **SAME LENGTH** - Keep approximately the same character count

VAGUE TERMS TO NEVER ADD (unless already present):
- "Machine Learning" (specific alternatives: NLP, Computer Vision, Deep Learning)
- "AI" (specific alternatives: LLMs, Transformers, Neural Networks)
- "Data Science" (specific alternatives: Statistical Modeling, Time Series Analysis)
- "Programming" (specific alternatives: Python, JavaScript, TypeScript)

EXAMPLE:
❌ BAD: Adding "Machine Learning" when not present
✅ GOOD: Reorganizing to put "PyTorch, TensorFlow, LangChain" at the front

CRITICAL OUTPUT FORMAT:
- Return ONLY the reorganized skills text
- NO explanations or metadata
- Just the skills list that goes directly into the resume"""

        try:
            response = generate_content_with_retry(
                client=self.genai_client,
                model='gemini-2.0-flash-exp',
                contents=prompt
            )

            new_skills = clean_gemini_response(response.text)

            # Replace first skills line
            if section['lines']:
                replacements.append({
                    'old_text': section['lines'][0]['text'],
                    'new_text': new_skills,
                    'type': 'skills_reorg'
                })
        except Exception as e:
            # Re-raise rate limit errors to allow retry mechanism to work
            error_str = str(e)
            if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str or 'quota' in error_str.lower():
                raise
            print(f"      ⚠️  Error reorganizing skills: {e}")

        return replacements

    def _parse_projects(self, lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Parse project entries from lines."""
        projects = []
        i = 0

        while i < len(lines):
            line = lines[i]

            # Project title is usually non-bullet, bold, or has certain patterns
            if line.get('bullet_level', 0) == 0 and len(line['text']) < 200:
                project = {
                    'title_text': line['text'],
                    'title_line': line,
                    'description_bullets': []
                }

                # Collect bullets for this project
                i += 1
                while i < len(lines) and lines[i].get('bullet_level', 0) > 0:
                    project['description_bullets'].append(lines[i])
                    i += 1

                if project['description_bullets']:  # Only add if has bullets
                    projects.append(project)
                continue

            i += 1

        return projects

    def _calculate_project_relevance(
        self,
        project: Dict[str, Any],
        keywords: List[str]
    ) -> float:
        """Calculate project relevance score."""
        score = 0

        # Check title
        title_lower = project['title_text'].lower()
        for keyword in keywords:
            if keyword.lower() in title_lower:
                score += 15  # Title mentions are worth more

        # Check description bullets
        for bullet in project['description_bullets']:
            text_lower = bullet['text'].lower()
            for keyword in keywords:
                if keyword.lower() in text_lower:
                    score += 5

        return min(100, score)  # Cap at 100


# ============================================================
# OVERFLOW RECOVERY IMPLEMENTATION
# ============================================================

class OverflowRecoveryComplete:
    """Complete implementation of overflow recovery."""

    def __init__(self, genai_client):
        self.genai_client = genai_client

    def recover_from_overflow(
        self,
        line_metadata: List[Dict[str, Any]],
        keywords: List[str],
        current_pages: int,
        target_pages: int,
        total_lines_added: int = 0,
        attempt: int = 1,
        max_attempts: int = 2
    ) -> Dict[str, Any]:
        """
        Multi-attempt overflow recovery with progressively aggressive strategies.
        
        Attempt 1: Condense 2+ line bullets
        Attempt 2: Remove entire low-relevance bullets
        
        Args:
            total_lines_added: The actual number of lines added during tailoring (from Phase 2)

        Returns:
            {
                'replacements': [...],
                'lines_condensed': int,
                'lines_removed': int,
                'estimated_lines_freed': int,
                'attempt': int
            }
        """
        print("\n" + "="*60)
        print(f"OVERFLOW RECOVERY - ATTEMPT {attempt}/{max_attempts}")
        print("="*60)
        print(f"   Current: {current_pages} pages")
        print(f"   Target: {target_pages} pages")
        
        # CONSERVATIVE APPROACH: Start with a small number and let multi-attempt handle it
        # Google Docs pagination is affected by many factors (fonts, spacing, formatting)
        # Better to under-remove and do another attempt than over-remove
        pages_overflow = current_pages - target_pages
        
        if total_lines_added > 0:
            # Use tracked value but cap it at a reasonable maximum
            lines_to_free = min(total_lines_added, pages_overflow * 5)
            print(f"   Lines added during tailoring: +{total_lines_added}")
            print(f"   Need to remove approximately: {lines_to_free} lines (capped for safety)")
        else:
            # VERY conservative fallback: 3 lines per page overflow
            # This accounts for the fact that page overflow might be just a few lines
            lines_to_free = pages_overflow * 3
            print(f"   Need to free: ~{lines_to_free} lines (conservative estimate)")
            print(f"   💡 Tip: If this doesn't work, attempt 2 will remove more")

        from space_borrowing import calculate_relevance_scores

        # Calculate relevance scores
        scored_lines = calculate_relevance_scores(line_metadata, keywords, "")

        # Find bullets
        bullets = [
            line for line in scored_lines
            if line.get('bullet_level', 0) > 0
        ]

        print(f"\n🔍 DEBUG: Found {len(bullets)} total bullets")

        # DEBUG: Show sample bullets with line counts
        if bullets:
            print(f"   Sample bullets:")
            for i, b in enumerate(bullets[:5]):
                bullet_text = b.get('text', '')
                actual_lines = sum(1 for line in bullet_text.split('\n') if line.strip())
                visual_lines_raw = b.get('visual_lines', 'N/A')
                print(f"   {i+1}. Lines: {actual_lines} (metadata says: {visual_lines_raw})")
                print(f"      Text: {bullet_text[:80]}...")
                print(f"      Has newlines: {'\\n' in bullet_text}")

        # FIX: visual_lines in metadata is off by 1 (counts blank line at end)
        # Correct it for all bullets
        for b in bullets:
            if 'visual_lines' in b and b['visual_lines'] > 1:
                b['visual_lines'] = b['visual_lines'] - 1
        
        print(f"\n   ✓ Corrected visual_lines (metadata was off by 1)")

        # Sort by: lowest relevance first
        bullets.sort(key=lambda x: (x.get('relevance_score', 50), -x.get('visual_lines', 1)))

        replacements = []

        if attempt == 1:
            # ATTEMPT 1: Condense 2+ line bullets ONE AT A TIME
            print(f"\n📝 Strategy: Condense bullets (2+ lines) INCREMENTALLY")
            print(f"   Will condense bullets ONE AT A TIME and check page count after each")

            # Filter to 2+ line bullets
            # IMPORTANT: Use visual_lines metadata, NOT newline count!
            # Bullets are single paragraphs - visual_lines tells us how many lines they span
            condensable = []
            for b in bullets:
                visual_lines = b.get('visual_lines', 1)
                if visual_lines >= 2:
                    condensable.append(b)

            print(f"   Found {len(condensable)} condensable bullets (2+ visual lines)")

            if not condensable:
                print(f"   ⚠️  No 2+ line bullets found to condense")
                return {
                    'replacements': [],
                    'lines_condensed': 0,
                    'lines_removed': 0,
                    'estimated_lines_freed': 0,
                    'attempt': attempt
                }
            
            # We'll process up to 10 bullets incrementally
            max_to_condense = min(10, len(condensable))
            condense_targets = condensable[:max_to_condense]

            print(f"\n🎯 Will try condensing up to {max_to_condense} bullets (checking after each):")
            for i, target in enumerate(condense_targets, 1):
                # Use visual_lines from metadata
                visual_lines = target.get('visual_lines', 1)
                print(f"   {i}. Relevance: {target.get('relevance_score', 0):.0f}/100, "
                      f"{visual_lines} visual lines")
                print(f"      {target['text'][:60]}...")

            for bullet in condense_targets:
                # Use visual_lines metadata instead of counting newlines
                current_visual_lines = bullet.get('visual_lines', 1)

                # OVERFLOW RECOVERY: We CAN condense 2-line bullets to 1 line when needed!
                # For 2-line bullets: condense to 1 line
                # For 3+ line bullets: reduce by 1 line (but not below 2)
                if current_visual_lines == 2:
                    target_visual_lines = 1  # OK to condense to 1 line during overflow
                else:
                    target_visual_lines = max(2, current_visual_lines - 1)

                # Estimate chars per visual line
                chars_per_line = bullet.get('char_limit', 80)
                target_char_count = int(target_visual_lines * chars_per_line)

                prompt = f"""AGGRESSIVELY condense this resume bullet to take up less space:

ORIGINAL ({len(bullet['text'])} chars, {current_visual_lines} visual lines):
{bullet['text']}

GOAL: Reduce to {target_visual_lines} line(s) (~{target_char_count} chars max)

CONTEXT: This is OVERFLOW RECOVERY. The resume is too long and we MUST condense to save space.
For 2-line bullets, it's OK to condense to 1 line when necessary.

REQUIREMENTS:
- Must save AT LEAST 1 full visual line (not just make it shorter)
- Remove ALL filler words: "various", "effectively", "successfully", "helped", etc.
- Keep ONLY core accomplishment and quantified data
- Use shortest possible phrasing while staying clear
- Target: ~{target_char_count} characters or less

Example transformations:
- "Successfully implemented various improvements to system performance across multiple areas" (3 lines) → "Improved system performance across multiple areas" (2 lines)
- "Worked collaboratively with cross-functional team to develop and deploy new features" (2 lines) → "Developed and deployed new features with cross-functional team" (1 line)
- "Built scalable data pipeline" (2 lines) → "Built scalable data pipeline" (1 line)

CRITICAL OUTPUT FORMAT:
- Return ONLY the condensed text
- NO labels, NO character counts, NO metadata
- Just the raw text for the resume

If the content cannot be meaningfully condensed to {target_visual_lines} line(s), return the original."""

                try:
                    response = generate_content_with_retry(
                        client=self.genai_client,
                        model='gemini-2.0-flash-exp',
                        contents=prompt
                    )

                    # Clean up artifacts from Gemini response
                    condensed = clean_gemini_response(response.text)

                    # Estimate visual lines for condensed text based on character count
                    chars_per_line = bullet.get('char_limit', 80)
                    condensed_chars = len(condensed)
                    condensed_visual_lines = max(1, (condensed_chars + chars_per_line - 1) // chars_per_line)

                    # Accept ONLY if we actually save at least 1 full visual line
                    # For 2-line bullets: must reduce to 1 line (not 2→2)
                    # For 3+ line bullets: must save at least 1 full line
                    is_acceptable = False
                    lines_saved = current_visual_lines - condensed_visual_lines
                    
                    if lines_saved >= 1:
                        # We saved at least 1 full line - accept it
                        is_acceptable = True
                    else:
                        # No visual lines saved (e.g., 2→2) - reject it
                        is_acceptable = False
                    
                    if is_acceptable:
                        replacements.append({
                            'old_text': bullet['text'],
                            'new_text': condensed,
                            'type': 'overflow_recovery_condense_incremental',  # Apply one-by-one
                            'bullet_text': bullet['text'][:50] + '...',
                            'lines_before': current_visual_lines,
                            'lines_after': condensed_visual_lines,
                            'relevance': bullet.get('relevance_score', 0)
                        })

                        reduction = len(bullet['text']) - len(condensed)
                        print(f"      ✓ Reduced by {reduction} chars ({current_visual_lines}→{condensed_visual_lines} lines): {condensed[:50]}...")
                    else:
                        print(f"      ⏭️  Skipped (no visual lines saved: {current_visual_lines}→{condensed_visual_lines}): {bullet['text'][:50]}...")

                except Exception as e:
                    # Re-raise rate limit errors to allow retry mechanism to work
                    error_str = str(e)
                    if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str or 'quota' in error_str.lower():
                        raise
                    print(f"      ⚠️  Error condensing: {e}")
            
            estimated_lines_freed = len(replacements) * 0.5
            
            return {
                'replacements': replacements,
                'lines_condensed': len(replacements),
                'lines_removed': 0,
                'estimated_lines_freed': estimated_lines_freed,
                'attempt': attempt
            }
            
        elif attempt == 2:
            # ATTEMPT 2: Ask Gemini to RANK bullets by removal priority (one-by-one removal)
            print(f"\n🗑️  Strategy: AI-powered incremental removal")
            print(f"   Will remove bullets ONE AT A TIME until page count is met")
            
            # Prepare bullet candidates for Gemini (limit to top 20 lowest relevance)
            candidates = bullets[:min(20, len(bullets))]
            
            # Build candidate list for Gemini
            candidate_info = []
            for i, bullet in enumerate(candidates, 1):
                # Count actual non-empty lines (ignore blank formatting lines)
                bullet_text = bullet.get('text', '')
                actual_lines = sum(1 for line in bullet_text.split('\n') if line.strip())
                actual_lines = max(1, actual_lines)  # At least 1
                
                candidate_info.append({
                    'id': i,
                    'text': bullet['text'][:100] + ('...' if len(bullet['text']) > 100 else ''),
                    'lines': actual_lines,
                    'relevance': int(bullet.get('relevance_score', 0))
                })
            
            # Ask Gemini to RANK bullets by removal priority
            prompt = f"""You are helping optimize a resume by ranking bullets for removal.

**Available Bullets (ID, Lines, Relevance, Text):**
{json.dumps(candidate_info, indent=2)}

**Your Task:**
RANK these bullets from "FIRST to remove" to "LAST to remove" based on their relevance and value.

**Guidelines:**
1. Bullets with relevance 0-10 should be removed first
2. Bullets with relevance 11-30 should be removed next
3. Bullets with higher relevance (30+) should be removed last
4. Within same relevance level, prioritize removing longer bullets first (more lines freed)

**Output Format (JSON only):**
{{
  "ranked_bullets": [3, 1, 7, 5, 2, ...],
  "reasoning": "Brief explanation of ranking strategy"
}}

Where `ranked_bullets` is an array of IDs in order (first ID = remove first, last ID = remove last).

Return ONLY valid JSON, no markdown, no extra text."""

            try:
                # Use lite model for fast bullet ranking
                response = generate_content_with_retry(
                    client=self.genai_client,
                    model='gemini-2.0-flash-lite',
                    contents=prompt
                )
                
                # Parse Gemini's response
                response_text = response.text.strip()
                response_text = re.sub(r'```json\s*', '', response_text)
                response_text = re.sub(r'```\s*', '', response_text)
                
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    selection = json.loads(json_match.group())
                    ranked_bullet_ids = selection.get('ranked_bullets', [])
                    reasoning = selection.get('reasoning', 'No reasoning provided')
                    
                    print(f"\n🤖 Gemini ranked {len(ranked_bullet_ids)} bullets for removal")
                    print(f"   Strategy: {reasoning}")
                    
                    # Convert IDs to actual bullets in ranked order
                    ranked_bullets = []
                    for bullet_id in ranked_bullet_ids:
                        if 1 <= bullet_id <= len(candidates):
                            ranked_bullets.append(candidates[bullet_id - 1])
                    
                    print(f"\n📋 Ranked removal order (top = remove first):")
                    for i, bullet in enumerate(ranked_bullets[:5], 1):  # Show top 5
                        bullet_text = bullet.get('text', '')
                        actual_lines = sum(1 for line in bullet_text.split('\n') if line.strip())
                        print(f"   {i}. Relevance: {bullet.get('relevance_score', 0):.0f}/100, {actual_lines} lines")
                        print(f"      {bullet['text'][:60]}...")
                    if len(ranked_bullets) > 5:
                        print(f"   ... and {len(ranked_bullets) - 5} more")
                    
                    # Create replacements in RANKED order (will be applied one-by-one by caller)
                    for bullet in ranked_bullets:
                        bullet_text = bullet['text']
                        if not bullet_text.endswith('\n'):
                            old_text = bullet_text + '\n'
                        else:
                            old_text = bullet_text
                            
                        replacements.append({
                            'old_text': old_text,
                            'new_text': '',
                            'type': 'overflow_recovery_remove_incremental',  # Special flag
                            'bullet_text': bullet['text'][:50] + '...',
                            'relevance': bullet.get('relevance_score', 0)
                        })
                    
                    estimated_lines_freed = len(ranked_bullets)  # Conservative estimate
                else:
                    print("   ⚠️  Failed to parse Gemini response, using relevance-based fallback")
                    # Fallback: use bullets sorted by relevance
                    for bullet in candidates:
                        bullet_text = bullet['text']
                        if not bullet_text.endswith('\n'):
                            old_text = bullet_text + '\n'
                        else:
                            old_text = bullet_text
                        replacements.append({
                            'old_text': old_text,
                            'new_text': '',
                            'type': 'overflow_recovery_remove_incremental',
                            'bullet_text': bullet['text'][:50] + '...',
                            'relevance': bullet.get('relevance_score', 0)
                        })
                    estimated_lines_freed = len(candidates)
                    
            except Exception as e:
                error_str = str(e)
                if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str or 'quota' in error_str.lower():
                    raise
                print(f"   ⚠️  Error with Gemini selection: {e}")
                # Fallback: remove lowest 3 bullets
                remove_targets = bullets[:3]
                for bullet in remove_targets:
                    bullet_text = bullet['text']
                    if not bullet_text.endswith('\n'):
                        old_text = bullet_text + '\n'
                    else:
                        old_text = bullet_text
                    replacements.append({
                        'old_text': old_text,
                        'new_text': '',
                        'type': 'overflow_recovery_remove'
                    })
                estimated_lines_freed = sum(max(1, b.get('visual_lines', 2) - 1) for b in remove_targets)
            
            return {
                'replacements': replacements,
                'lines_condensed': 0,
                'lines_removed': len(replacements),
                'estimated_lines_freed': estimated_lines_freed,
                'attempt': attempt
            }
        
        # Shouldn't reach here
        return {
            'replacements': [],
            'lines_condensed': 0,
            'lines_removed': 0,
            'estimated_lines_freed': 0,
            'attempt': attempt
        }


# ============================================================
# MAIN ORCHESTRATOR
# ============================================================

def run_systematic_tailoring(
    job_description: str,
    job_keywords: List[str],
    line_metadata: List[Dict[str, Any]],
    resume_text: str,
    mimikree_responses: Dict[str, str],
    mimikree_formatted_data: str,
    skip_space_borrowing: bool = False,
    conservative_mode: bool = True
) -> Dict[str, Any]:
    """
    Main entry point for systematic tailoring.

    Args:
        job_description: Full job description
        job_keywords: Extracted prioritized keywords
        line_metadata: Document structure from extract_document_structure
        resume_text: Plain text of current resume
        mimikree_responses: Dict of Mimikree question -> response
        mimikree_formatted_data: Formatted Mimikree data string
        skip_space_borrowing: If True, only runs Phase 1 (keyword validation/replacement) without Phase 2 (space borrowing)
        conservative_mode: If True, only edits Profile → Skills → Projects (strategic changes only)
                          If False, edits everything including experience bullets (aggressive)

    Returns:
        {
            'phase1_results': {...},
            'phase2_results': {...} or None,
            'overflow_recovery': {...} or None,
            'all_replacements': [...]
        }
    """
    # Initialize Gemini
    api_key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
    client = genai.Client(api_key=api_key)

    # ============================================================
    # PHASE 1: KEYWORD VALIDATION
    # ============================================================

    validator = KeywordValidatorComplete(mimikree_responses)
    phase1_results = validator.execute_phase_1(job_keywords, resume_text)

    if not phase1_results['should_proceed']:
        print("\n⚠️  Low keyword match detected. Proceeding with limited tailoring...")

    # ============================================================
    # PHASE 2: SYSTEMATIC EDITING (Space Borrowing)
    # ============================================================
    # CRITICAL: Only run Phase 2 if there's page overflow
    # Phase 2 condenses low-relevance bullets and expands high-relevance ones
    # This should ONLY happen when we need to compensate for added content

    phase2_results = None
    if not skip_space_borrowing:
        if conservative_mode:
            print("\n📊 Running Phase 2: Conservative Strategic Editing")
            print("   Focus: Profile → Skills → Projects only")
        else:
            print("\n📊 Running Phase 2: Space Borrowing (condensing donors, expanding receivers)")
        editor = SystematicEditorComplete(client)
        phase2_results = editor.execute_phase_2(
            line_metadata,
            phase1_results,
            mimikree_formatted_data,
            job_description,
            conservative_mode
        )
        all_replacements = phase2_results['replacements']
    else:
        print("\n⏭️  Skipping Phase 2: No page overflow detected")
        all_replacements = []

    return {
        'phase1_results': phase1_results,
        'phase2_results': phase2_results,
        'all_replacements': all_replacements
    }


def recover_from_overflow_if_needed(
    line_metadata: List[Dict[str, Any]],
    keywords: List[str],
    current_pages: int,
    target_pages: int,
    total_lines_added: int = 0,
    attempt: int = 1
) -> Dict[str, Any]:
    """
    Run overflow recovery if needed.
    
    Args:
        total_lines_added: The actual number of lines added during tailoring

    Returns recovery results with replacement list.
    """
    api_key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
    client = genai.Client(api_key=api_key)

    recovery = OverflowRecoveryComplete(client)
    return recovery.recover_from_overflow(
        line_metadata,
        keywords,
        current_pages,
        target_pages,
        total_lines_added,
        attempt
    )
