"""
Space Borrowing Logic for Resume Tailoring
Allows borrowing visual lines from less relevant content to expand more relevant content.
"""

from typing import List, Dict, Any, Tuple


def calculate_relevance_scores(
    line_metadata: List[Dict[str, Any]],
    job_keywords: List[str],
    job_description: str
) -> List[Dict[str, Any]]:
    """
    Calculate relevance scores for each line based on keyword presence.

    Args:
        line_metadata: List of line metadata dictionaries
        job_keywords: List of important keywords from job description
        job_description: Full job description text

    Returns:
        line_metadata with added 'relevance_score' field
    """
    for line in line_metadata:
        text_lower = line['text'].lower()
        score = 0

        # Count keyword matches
        for keyword in job_keywords:
            if keyword.lower() in text_lower:
                score += 10  # Each keyword match = 10 points

        # Bonus for quantified data (metrics are always valuable)
        if any(char.isdigit() for char in line['text']):
            score += 5

        # Penalty if line is too short (likely a header or low-content line)
        if line['current_length'] < 30:
            score -= 5

        line['relevance_score'] = max(0, score)

    return line_metadata


def identify_space_borrowing_opportunities(
    line_metadata: List[Dict[str, Any]],
    job_keywords: List[str]
) -> Dict[str, Any]:
    """
    Identify opportunities to borrow space from low-relevance lines to expand high-relevance lines.

    Args:
        line_metadata: List of line metadata with relevance scores
        job_keywords: List of important keywords

    Returns:
        Dictionary with borrowing opportunities
    """
    # Calculate relevance scores if not already done
    if 'relevance_score' not in line_metadata[0]:
        line_metadata = calculate_relevance_scores(line_metadata, job_keywords, "")

    # Identify lines with high char_buffer (underutilized space)
    donor_lines = []
    for line in line_metadata:
        char_buffer = line.get('char_buffer', 0)
        relevance = line.get('relevance_score', 0)

        # Donor candidates: low relevance + high buffer OR wraps to multiple lines with low relevance
        if (relevance < 10 and char_buffer > 30) or (relevance < 10 and line.get('visual_lines', 1) > 1):
            donor_lines.append({
                'line_number': line['line_number'],
                'text_preview': line['text'][:60] + '...' if len(line['text']) > 60 else line['text'],
                'relevance': relevance,
                'char_buffer': char_buffer,
                'visual_lines': line.get('visual_lines', 1),
                'can_remove_lines': line.get('visual_lines', 1) - 1,  # Keep at least 1 line
                'potential_chars_freed': char_buffer + (line.get('visual_lines', 1) - 1) * line.get('char_limit_continuation', 50)
            })

    # Identify lines that could benefit from expansion
    receiver_lines = []
    for line in line_metadata:
        char_buffer = line.get('char_buffer', 0)
        relevance = line.get('relevance_score', 0)

        # Receiver candidates: high relevance + low buffer
        if relevance >= 20 and char_buffer < 20:
            receiver_lines.append({
                'line_number': line['line_number'],
                'text_preview': line['text'][:60] + '...' if len(line['text']) > 60 else line['text'],
                'relevance': relevance,
                'char_buffer': char_buffer,
                'visual_lines': line.get('visual_lines', 1),
                'could_use_chars': 100  # Could benefit from ~100 more chars for elaboration
            })

    # Sort by potential
    donor_lines.sort(key=lambda x: (x['relevance'], -x['potential_chars_freed']))
    receiver_lines.sort(key=lambda x: -x['relevance'])

    return {
        'donor_lines': donor_lines,
        'receiver_lines': receiver_lines,
        'total_borrowable_chars': sum(d['potential_chars_freed'] for d in donor_lines),
        'total_borrowable_lines': sum(d['can_remove_lines'] for d in donor_lines),
        'total_expansion_needed': sum(r['could_use_chars'] for r in receiver_lines),
    }


def create_borrowing_instructions(borrowing_opportunities: Dict[str, Any]) -> str:
    """
    Create instructions for the AI model on how to borrow space.

    Args:
        borrowing_opportunities: Output from identify_space_borrowing_opportunities

    Returns:
        Formatted instructions string
    """
    donor_lines = borrowing_opportunities['donor_lines']
    receiver_lines = borrowing_opportunities['receiver_lines']

    if not donor_lines or not receiver_lines:
        return ""

    instructions = f"""
SPACE BORROWING STRATEGY:
You have {borrowing_opportunities['total_borrowable_lines']} visual lines available from low-relevance content.
Use this space to expand high-relevance content.

LOW-RELEVANCE CONTENT (can be shortened/condensed):
"""

    for i, donor in enumerate(donor_lines[:5], 1):  # Top 5
        instructions += f"""
  {i}. Line {donor['line_number']}: "{donor['text_preview']}"
      Relevance: {donor['relevance']}/100, Can free: {donor['can_remove_lines']} line(s)
      Action: Condense this to fewer lines or reduce detail"""

    instructions += f"""

HIGH-RELEVANCE CONTENT (should be expanded):
"""

    for i, receiver in enumerate(receiver_lines[:5], 1):  # Top 5
        instructions += f"""
  {i}. Line {receiver['line_number']}: "{receiver['text_preview']}"
      Relevance: {receiver['relevance']}/100, Needs: {receiver['could_use_chars']} chars
      Action: Add more detail from Mimikree data, elaborate on impact"""

    instructions += f"""

STRATEGY:
1. Shorten/condense the {len(donor_lines)} low-relevance items above
2. Use the freed space to elaborate on the {len(receiver_lines)} high-relevance items
3. Keep total visual lines the same - you're redistributing space intelligently
4. Prioritize truthful expansion using Mimikree data only
"""

    return instructions


def generate_space_aware_prompt_additions(
    line_metadata: List[Dict[str, Any]],
    job_keywords: List[str]
) -> str:
    """
    Generate additional prompt instructions based on space borrowing analysis.

    Args:
        line_metadata: List of line metadata
        job_keywords: List of important keywords

    Returns:
        Formatted prompt additions
    """
    # Calculate relevance scores
    line_metadata = calculate_relevance_scores(line_metadata, job_keywords, "")

    # Identify borrowing opportunities
    borrowing = identify_space_borrowing_opportunities(line_metadata, job_keywords)

    # Create instructions
    if borrowing['donor_lines'] and borrowing['receiver_lines']:
        instructions = create_borrowing_instructions(borrowing)

        return f"""
{instructions}

CONTENT REDISTRIBUTION RULES:
- NEVER invent accomplishments or data
- Only expand using truthful Mimikree data
- Condense low-relevance items by removing filler words, not facts
- When expanding, add context, methodology, or business impact
- Maintain the same total page length by balancing expansions with condensing
"""

    return ""
