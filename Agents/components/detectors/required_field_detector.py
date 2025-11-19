"""
Required Field Detector

Multi-layer detection system to identify which form fields are required vs optional.
This reduces unnecessary user input requests by filtering out optional fields.

Detection Layers:
1. HTML Attribute Detection (required, aria-required)
2. Visual Indicator Detection (asterisks, red stars, text markers)
3. Form Validation Inference (EEO patterns, opt-out options)
4. Smart Heuristics (field label patterns, option analysis)
"""

import re
from typing import Dict, List, Any, Optional
from playwright.async_api import Page, Frame, Locator
from loguru import logger


class RequiredFieldDetector:
    """Detects whether form fields are required or optional using multi-layer analysis."""
    
    # EEO/Demographic field patterns (always optional by law)
    EEO_PATTERNS = [
        r'gender', r'sex', r'pronoun', r'transgender',
        r'race', r'ethnicity', r'hispanic', r'latino',
        r'veteran', r'disability', r'disabled',
        r'sexual orientation', r'lgbtq', r'diversity',
        r'religion', r'age', r'date of birth',
        r'marital status', r'citizenship'
    ]
    
    # Optional field label patterns
    OPTIONAL_PATTERNS = [
        r'\(optional\)', r'\[optional\]', r'optional:',
        r'preferred name', r'name pronunciation',
        r'how did you hear', r'referral source',
        r'middle name', r'suffix', r'prefix'
    ]
    
    def __init__(self, context: Page | Frame):
        self.context = context
    
    async def is_field_required(self, field: Dict[str, Any]) -> Dict[str, Any]:
        """
        Determine if a form field is required or optional.
        
        Returns:
            {
                'is_required': bool,
                'confidence': float (0-1),
                'detection_method': str,
                'indicators': List[str]
            }
        """
        indicators = []
        confidence = 0.5  # Default: uncertain
        is_required = True  # Default to required (safe approach)
        detection_method = 'default'
        
        try:
            field_label = field.get('label', '').lower()
            field_category = field.get('field_category', '')
            element = field.get('element')
            
            # Layer 1: HTML Attribute Detection (highest confidence)
            html_result = await self._check_html_required(field, element)
            if html_result['detected']:
                is_required = html_result['is_required']
                confidence = html_result['confidence']
                detection_method = html_result['method']
                indicators.extend(html_result['indicators'])
                
                # If we have high confidence from HTML, we can return early
                if confidence >= 0.9:
                    return {
                        'is_required': is_required,
                        'confidence': confidence,
                        'detection_method': detection_method,
                        'indicators': indicators
                    }
            
            # Layer 2: Visual Indicator Detection
            visual_result = await self._check_visual_indicators(field, element)
            if visual_result['detected']:
                # Visual indicators can override or confirm HTML detection
                if visual_result['confidence'] > confidence:
                    is_required = visual_result['is_required']
                    confidence = visual_result['confidence']
                    detection_method = visual_result['method']
                indicators.extend(visual_result['indicators'])
            
            # Layer 3: EEO Pattern Detection (always overrides - these are always optional)
            if self._is_eeo_question(field_label):
                is_required = False
                confidence = 0.95
                detection_method = 'eeo_pattern'
                indicators.append('EEO/demographic question (optional by law)')
                return {
                    'is_required': is_required,
                    'confidence': confidence,
                    'detection_method': detection_method,
                    'indicators': indicators
                }
            
            # Layer 4: Options Analysis (for radio/checkbox groups)
            if field_category in ['radio_group', 'checkbox_group']:
                options_result = self._check_options_for_opt_out(field)
                if options_result['has_opt_out']:
                    is_required = False
                    confidence = 0.85
                    detection_method = 'opt_out_option_available'
                    indicators.append(f"Has opt-out option: {options_result['opt_out_text']}")
                    return {
                        'is_required': is_required,
                        'confidence': confidence,
                        'detection_method': detection_method,
                        'indicators': indicators
                    }
            
            # Layer 5: Label Pattern Detection
            if self._has_optional_pattern(field_label):
                is_required = False
                confidence = 0.80
                detection_method = 'optional_label_pattern'
                indicators.append('Label contains optional indicator')
            
            # If still uncertain, default to required (safe approach)
            if confidence < 0.6:
                is_required = True
                confidence = 0.5
                detection_method = 'default_safe'
                indicators.append('Uncertain - defaulting to required for safety')
            
        except Exception as e:
            logger.error(f"Error detecting required status for field: {e}")
            # On error, default to required
            is_required = True
            confidence = 0.5
            detection_method = 'error_default'
            indicators.append(f'Detection error: {str(e)}')
        
        return {
            'is_required': is_required,
            'confidence': confidence,
            'detection_method': detection_method,
            'indicators': indicators
        }
    
    async def _check_html_required(self, field: Dict[str, Any], element: Optional[Locator]) -> Dict[str, Any]:
        """Layer 1: Check HTML attributes for required status."""
        result = {
            'detected': False,
            'is_required': True,
            'confidence': 0.5,
            'method': 'html_attribute',
            'indicators': []
        }
        
        try:
            # Check if field dict already has required flag from field detection
            if 'required' in field:
                required_attr = field.get('required', False)
                if required_attr:
                    result['detected'] = True
                    result['is_required'] = True
                    result['confidence'] = 0.95
                    result['indicators'].append('HTML required attribute present')
                    return result
                else:
                    # Absence of required attribute suggests optional
                    result['detected'] = True
                    result['is_required'] = False
                    result['confidence'] = 0.70
                    result['indicators'].append('No HTML required attribute')
                    return result
            
            # Fallback: check element directly if available
            if element:
                # Check required attribute
                required_attr = await element.get_attribute('required')
                if required_attr is not None:
                    result['detected'] = True
                    result['is_required'] = True
                    result['confidence'] = 0.95
                    result['indicators'].append('HTML required attribute present')
                    return result
                
                # Check aria-required
                aria_required = await element.get_attribute('aria-required')
                if aria_required == 'true':
                    result['detected'] = True
                    result['is_required'] = True
                    result['confidence'] = 0.90
                    result['indicators'].append('aria-required="true"')
                    return result
                
                # Check data-required or similar
                data_required = await element.get_attribute('data-required')
                if data_required in ['true', 'True', '1', 'required']:
                    result['detected'] = True
                    result['is_required'] = True
                    result['confidence'] = 0.85
                    result['indicators'].append(f'data-required="{data_required}"')
                    return result
                
                # No required attributes found
                result['detected'] = True
                result['is_required'] = False
                result['confidence'] = 0.65
                result['indicators'].append('No required/aria-required attributes')
        
        except Exception as e:
            logger.debug(f"Error checking HTML required: {e}")
        
        return result
    
    async def _check_visual_indicators(self, field: Dict[str, Any], element: Optional[Locator]) -> Dict[str, Any]:
        """Layer 2: Check for visual indicators like asterisks or color."""
        result = {
            'detected': False,
            'is_required': True,
            'confidence': 0.5,
            'method': 'visual_indicator',
            'indicators': []
        }
        
        try:
            field_label = field.get('label', '')
            
            # Check for asterisk in label
            if '*' in field_label:
                # Check if asterisk is at the end (common pattern)
                if re.search(r'\*\s*$', field_label.strip()):
                    result['detected'] = True
                    result['is_required'] = True
                    result['confidence'] = 0.85
                    result['indicators'].append('Asterisk (*) in label')
                    return result
            
            # Check for "(required)" or "[required]" text
            if re.search(r'\(required\)|\[required\]|required:', field_label, re.IGNORECASE):
                result['detected'] = True
                result['is_required'] = True
                result['confidence'] = 0.90
                result['indicators'].append('Text marker: (required)')
                return result
            
            # Check for "(optional)" or "[optional]" text
            if re.search(r'\(optional\)|\[optional\]|optional:', field_label, re.IGNORECASE):
                result['detected'] = True
                result['is_required'] = False
                result['confidence'] = 0.90
                result['indicators'].append('Text marker: (optional)')
                return result
            
            # Check element and parent for required CSS classes
            if element:
                # Check element classes
                element_class = await element.get_attribute('class') or ''
                if any(cls in element_class.lower() for cls in ['required', 'is-required', 'mandatory']):
                    result['detected'] = True
                    result['is_required'] = True
                    result['confidence'] = 0.80
                    result['indicators'].append(f'Required CSS class: {element_class}')
                    return result
                
                # Check for red asterisk or required indicator near the label
                try:
                    # Look for span.required or similar near the field
                    parent = element.locator('xpath=..')
                    required_indicator = await parent.locator('.required, .asterisk, [class*="required"], span:has-text("*")').count()
                    
                    if required_indicator > 0:
                        result['detected'] = True
                        result['is_required'] = True
                        result['confidence'] = 0.75
                        result['indicators'].append('Required indicator element found near field')
                        return result
                except:
                    pass
        
        except Exception as e:
            logger.debug(f"Error checking visual indicators: {e}")
        
        return result
    
    def _is_eeo_question(self, field_label: str) -> bool:
        """Layer 3: Check if field matches EEO/demographic patterns."""
        field_label_lower = field_label.lower()
        
        for pattern in self.EEO_PATTERNS:
            if re.search(pattern, field_label_lower):
                return True
        
        return False
    
    def _check_options_for_opt_out(self, field: Dict[str, Any]) -> Dict[str, Any]:
        """Layer 4: Check if radio/checkbox group has opt-out options."""
        result = {
            'has_opt_out': False,
            'opt_out_text': None
        }
        
        try:
            options = field.get('options', [])
            if not options:
                return result
            
            # Common opt-out option patterns
            opt_out_patterns = [
                'prefer not to answer',
                'decline to self-identify',
                'decline to answer',
                'i prefer not to say',
                'prefer not to say',
                'choose not to answer',
                'do not wish to answer',
                'n/a',
                'none of the above'
            ]
            
            # Extract option texts
            option_texts = []
            for opt in options:
                if isinstance(opt, dict):
                    opt_text = opt.get('text', '').lower()
                elif isinstance(opt, str):
                    opt_text = opt.lower()
                else:
                    continue
                
                option_texts.append(opt_text)
                
                # Check if this option is an opt-out
                for pattern in opt_out_patterns:
                    if pattern in opt_text:
                        result['has_opt_out'] = True
                        result['opt_out_text'] = opt_text
                        return result
            
        except Exception as e:
            logger.debug(f"Error checking options for opt-out: {e}")
        
        return result
    
    def _has_optional_pattern(self, field_label: str) -> bool:
        """Layer 5: Check if label matches known optional field patterns."""
        field_label_lower = field_label.lower()
        
        for pattern in self.OPTIONAL_PATTERNS:
            if re.search(pattern, field_label_lower):
                return True
        
        return False

