"""Intelligent dropdown selector that uses profile data and question context."""

import logging
import re
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class IntelligentDropdownSelector:
    """Selects dropdown options intelligently using profile data and question analysis."""
    
    @staticmethod
    def select_from_options(
        question: str,
        options: List[str],
        profile: Optional[Dict[str, Any]] = None,
        target_value: Optional[str] = None
    ) -> Optional[str]:
        """
        Intelligently select the best option from a dropdown based on question context and profile.
        
        Args:
            question: The field label/question
            options: List of available option texts
            profile: User profile data
            target_value: The value we're trying to fill (if known)
            
        Returns:
            Selected option text or None if no good match
        """
        if not options:
            return None
        
        question_lower = question.lower()
        
        # 1. If we have a target value, try exact or partial match first
        if target_value:
            target_lower = target_value.lower()
            
            # Exact match
            for opt in options:
                if opt.lower() == target_lower:
                    logger.info(f"📍 Exact match: '{opt}' for '{target_value}'")
                    return opt
            
            # Partial match with length validation (avoid "US +1" matching "United States")
            best_match = None
            best_ratio = float('inf')
            for opt in options:
                opt_lower = opt.lower()
                if target_lower in opt_lower or opt_lower in target_lower:
                    # Prefer matches with similar lengths
                    len_ratio = max(len(opt), len(target_value)) / max(min(len(opt), len(target_value)), 1)
                    if len_ratio < 2.0 and len_ratio < best_ratio:
                        best_match = opt
                        best_ratio = len_ratio
            
            if best_match:
                logger.info(f"📍 Partial match: '{best_match}' for '{target_value}' (ratio: {best_ratio:.2f})")
                return best_match
        
        # 2. Use profile data for intelligent selection
        if profile:
            # Gender questions
            if any(word in question_lower for word in ['gender', 'sex']):
                gender = profile.get('gender', '').lower()
                if gender:
                    for opt in options:
                        opt_lower = opt.lower()
                        if gender in opt_lower or (gender == 'male' and 'man' in opt_lower) or (gender == 'female' and 'woman' in opt_lower):
                            logger.info(f"👤 Gender match from profile: '{opt}'")
                            return opt
                # Prefer "Prefer not to say" if available and no gender in profile
                for opt in options:
                    if 'prefer not' in opt.lower() or 'decline' in opt.lower():
                        logger.info(f"👤 Gender default: '{opt}'")
                        return opt
            
            # Work authorization questions
            if any(word in question_lower for word in ['authorized', 'authorization', 'legally work', 'lawfully work', 'work authorization']):
                auth = profile.get('work_authorization', '').lower()
                visa = profile.get('visa_status', '').lower()
                
                if 'yes' in auth or 'citizen' in auth or 'authorized' in auth or 'yes' in visa:
                    for opt in options:
                        if opt.lower() in ['yes', 'y', 'authorized', 'citizen', 'eligible']:
                            logger.info(f"🔐 Work auth YES from profile: '{opt}'")
                            return opt
                elif 'no' in auth or 'sponsor' in auth or 'no' in visa:
                    for opt in options:
                        if opt.lower() in ['no', 'n', 'require sponsor', 'not authorized']:
                            logger.info(f"🔐 Work auth NO from profile: '{opt}'")
                            return opt
            
            # Sponsorship questions
            if any(word in question_lower for word in ['sponsor', 'visa sponsor', 'immigration']):
                visa = profile.get('visa_status', '').lower()
                auth = profile.get('work_authorization', '').lower()
                
                if 'sponsor' in visa or 'h1b' in visa or 'require' in visa:
                    for opt in options:
                        if opt.lower() in ['yes', 'y', 'required', 'need sponsor']:
                            logger.info(f"🔐 Sponsorship YES from profile: '{opt}'")
                            return opt
                else:
                    for opt in options:
                        if opt.lower() in ['no', 'n', 'not required', 'not needed']:
                            logger.info(f"🔐 Sponsorship NO from profile: '{opt}'")
                            return opt
            
            # Veteran status
            if 'veteran' in question_lower:
                veteran = profile.get('veteran_status', '').lower()
                if 'yes' in veteran:
                    for opt in options:
                        if opt.lower() in ['yes', 'y', 'veteran']:
                            logger.info(f"🎖️ Veteran YES from profile: '{opt}'")
                            return opt
                else:
                    for opt in options:
                        if opt.lower() in ['no', 'n', 'not a veteran', 'non-veteran']:
                            logger.info(f"🎖️ Veteran NO from profile: '{opt}'")
                            return opt
            
            # Disability status
            if 'disab' in question_lower:
                disability = profile.get('disability_status', '').lower()
                if 'yes' in disability or 'have' in disability:
                    for opt in options:
                        if opt.lower() in ['yes', 'y']:
                            logger.info(f"♿ Disability YES from profile: '{opt}'")
                            return opt
                elif 'prefer not' in disability or 'decline' in disability:
                    for opt in options:
                        if 'prefer not' in opt.lower() or 'decline' in opt.lower():
                            logger.info(f"♿ Disability prefer not: '{opt}'")
                            return opt
                else:
                    for opt in options:
                        if opt.lower() in ['no', 'n']:
                            logger.info(f"♿ Disability NO from profile: '{opt}'")
                            return opt
            
            # Race/Ethnicity
            if any(word in question_lower for word in ['race', 'ethnicity', 'ethnic']):
                race = profile.get('race_ethnicity', '') or profile.get('ethnicity', '')
                if race:
                    race_lower = race.lower()
                    for opt in options:
                        if race_lower in opt.lower() or opt.lower() in race_lower:
                            logger.info(f"🌍 Race/ethnicity from profile: '{opt}'")
                            return opt
                # Prefer "Prefer not to say" if no race data
                for opt in options:
                    if 'prefer not' in opt.lower() or 'decline' in opt.lower():
                        logger.info(f"🌍 Race/ethnicity default: '{opt}'")
                        return opt
        
        # 3. Default Yes/No logic
        if all(opt.lower() in ['yes', 'no', 'y', 'n'] for opt in options if opt.strip()):
            # For yes/no questions, default to 'No' for safety unless it's about being a good fit
            if any(word in question_lower for word in ['good fit', 'interested', 'willing', 'able', 'qualified']):
                for opt in options:
                    if opt.lower() in ['yes', 'y']:
                        logger.info(f"✅ Default YES for positive question: '{opt}'")
                        return opt
            else:
                for opt in options:
                    if opt.lower() in ['no', 'n']:
                        logger.info(f"❌ Default NO for safety: '{opt}'")
                        return opt
        
        logger.debug(f"No intelligent match found for question: {question}")
        return None


