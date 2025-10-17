# Adzuna Multi-Button Flow - Complete Implementation

## Overview
This document describes the complete implementation of Adzuna's special multi-button application flow, where job applications require clicking through 2-3 intermediate pages before reaching the actual application form. The number of buttons depends on whether Adzuna redirects through a land page or goes directly to DeJobs.

---

## 🔄 The Multi-Button Flow (2 or 3 buttons)

### Route 1: Direct to DeJobs (2 buttons - most common)
```
┌─────────────────────────────────────────────┐
│ BUTTON 1: Adzuna Details Page              │
│ URL: adzuna.com/details/XXXXXXX             │
├─────────────────────────────────────────────┤
│ 1. Close "Email Alert" popup                │
│    - Click "No, thanks" button              │
│ 2. Click "Apply for this job" button        │
│    - Green button with data-js="apply"      │
└─────────────────────────────────────────────┘
              ↓ (direct redirect)
┌─────────────────────────────────────────────┐
│ BUTTON 2: DeJobs Intermediate Page          │
│ URL: dejobs.org/*/job/XXXXXXX               │
├─────────────────────────────────────────────┤
│ Click "Apply Now" button                    │
│    - Opens actual application in new tab    │
└─────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────┐
│ ACTUAL APPLICATION FORM                     │
│ URL: Various (Greenhouse, Workday, etc.)    │
├─────────────────────────────────────────────┤
│ ✅ Normal form filling begins                │
│ ✅ Action sequence cleared (2 buttons saved) │
│ ✅ All actions still recorded for logging    │
└─────────────────────────────────────────────┘
```

### Route 2: Through Land Page (3 buttons - less common)
```
┌─────────────────────────────────────────────┐
│ BUTTON 1: Adzuna Details Page              │
│ URL: adzuna.com/details/XXXXXXX             │
├─────────────────────────────────────────────┤
│ 1. Close "Email Alert" popup                │
│    - Click "No, thanks" button              │
│ 2. Click "Apply for this job" button        │
│    - Green button with data-js="apply"      │
└─────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────┐
│ BUTTON 2: Adzuna Land Page                  │
│ URL: adzuna.com/land/ad/XXXXXXX             │
├─────────────────────────────────────────────┤
│ Click "Apply Now" button                    │
│    - Links to rr.jobsyn.org (DeJobs)        │
└─────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────┐
│ BUTTON 3: DeJobs Intermediate Page          │
│ URL: dejobs.org/*/job/XXXXXXX               │
├─────────────────────────────────────────────┤
│ Click "Apply Now" button                    │
│    - Opens actual application in new tab    │
└─────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────┐
│ ACTUAL APPLICATION FORM                     │
│ URL: Various (Greenhouse, Workday, etc.)    │
├─────────────────────────────────────────────┤
│ ✅ Normal form filling begins                │
│ ✅ Action sequence cleared (3 buttons saved) │
│ ✅ All actions still recorded for logging    │
└─────────────────────────────────────────────┘
```

---

## 📋 Implementation Details

### 1. Apply Detector (`apply_detector.py`)

Added two new handler methods for Adzuna's special flow:

#### `_handle_adzuna_details_page()` (Lines 143-198)
**Purpose:** Handle the first Adzuna page with email popup and apply button

**Actions:**
1. Wait 2 seconds for popup to appear
2. Try to close email alert popup by clicking "No, thanks"
   - Selectors tried:
     - `a.ea_close:has-text("No, thanks")`
     - `div.mfp-content a.ea_close:has-text("No, thanks")`
     - `a[class*="ea_close"]:has-text("No, thanks")`
     - `.ea_close`
3. Find and return "Apply for this job" button
   - Selectors tried:
     - `a[data-js="apply"]:has-text("Apply for this job")`
     - `a[data-js="apply"]`
     - `a.bg-adzuna-green-500:has-text("Apply for this job")`
     - `a:has-text("Apply for this job")`

**Returns:**
```python
{
    'element': <Locator>,
    'confidence': 0.95,
    'reason': 'Adzuna details page - Apply for this job button',
    'method': 'adzuna_details_page'
}
```

#### `_handle_adzuna_land_page()` (Lines 200-230)
**Purpose:** Handle the Adzuna intermediate landing page

**Actions:**
1. Wait 1 second for page to load
2. Find "Apply Now" button that links to DeJobs
   - Selectors tried:
     - `a[href*="rr.jobsyn.org"]:has-text("Apply Now")`
     - `a.bg-button:has-text("Apply Now")`
     - `a[class*="bg-button"]:has-text("Apply Now")`
     - `a:has-text("Apply Now")`

**Returns:**
```python
{
    'element': <Locator>,
    'confidence': 0.95,
    'reason': 'Adzuna land page - Apply Now button to DeJobs',
    'method': 'adzuna_land_page'
}
```

#### URL Detection Logic (Lines 56-64)
```python
# Check for Adzuna-specific flow
current_url = self.page.url
if "adzuna.com" in current_url:
    if "/details/" in current_url:
        return await self._handle_adzuna_details_page()
    elif "/land/ad/" in current_url:
        return await self._handle_adzuna_land_page()
```

---

### 2. Job Application Agent (`job_application_agent.py`)

Updated `_state_click_apply()` method with comprehensive flow tracking:

#### Flow Tracking (Lines 1266-1322)

**BUTTON 1 Detection:**
```python
if "adzuna.com/details/" in current_url:
    state.context['adzuna_button_1_clicked'] = True
    state.context['adzuna_flow_active'] = True
```

**BUTTON 2 Detection:**
```python
if "adzuna.com/land/ad/" in current_url and state.context.get('adzuna_flow_active'):
    state.context['adzuna_button_2_clicked'] = True
    state.context['has_clicked_apply'] = False  # Not yet on actual application
    return 'ai_guided_navigation'
```

**BUTTON 3 Detection:**
```python
if "dejobs.org" in current_url and "/job/" in current_url and state.context.get('adzuna_flow_active'):
    state.context['adzuna_button_3_clicked'] = True
    state.context['on_dejobs_intermediate'] = True
    state.context['has_clicked_apply'] = False  # Not yet on actual application
    return 'ai_guided_navigation'
```

**Reached Actual Application:**
```python
if (state.context.get('adzuna_flow_active') and 
    "adzuna.com" not in current_url and 
    "dejobs.org" not in current_url):
    
    # Clear preliminary action sequence
    action_seq = state.context.get('action_sequence', [])
    if len(action_seq) >= 3:
        preliminary_actions = action_seq[:3]
        state.context['adzuna_preliminary_actions'] = preliminary_actions
        state.context['action_sequence'] = action_seq[3:]
    
    # Reset flags
    state.context['adzuna_flow_active'] = False
    state.context['adzuna_flow_completed'] = True
    state.context['has_clicked_apply'] = True
```

---

### 3. Auth Page Detector (`auth_page_detector.py`)

Added bypasses to prevent false positives on job application pages:

#### Adzuna Bypass (Lines 61-64)
```python
if "adzuna.com" in current_url:
    logger.info("ℹ️ Skipping auth detection for Adzuna job page")
    return None
```

#### DeJobs Bypass (Lines 66-69)
```python
if "dejobs.org" in current_url and "/job/" in current_url:
    logger.info("ℹ️ Skipping auth detection for DeJobs application page")
    return None
```

#### Generic Job Page Bypass (Lines 71-81)
```python
job_url_patterns = ['/jobs/', '/job/', '/apply/', '/application/', '/careers/']
if any(pattern in current_url.lower() for pattern in job_url_patterns):
    apply_indicators = await self.page.locator('button:has-text("apply"), a:has-text("apply")').count()
    if apply_indicators > 0:
        logger.info("ℹ️ Skipping auth detection - detected job application page with apply button")
        return None
```

---

## 🎯 Key Features

### 1. **Action History Management**
- **Records all actions:** All 3 preliminary button clicks are recorded in the ActionRecorder for logging and debugging
- **Clears sequence:** The `action_sequence` state variable is cleared of the 3 preliminary actions
- **Saves reference:** Preliminary actions are saved in `state.context['adzuna_preliminary_actions']` for reference
- **Result:** Clean slate for actual form filling, but complete audit trail preserved

### 2. **State Tracking**
The agent tracks progress through the flow using these context flags:
- `adzuna_button_1_clicked` - Clicked "Apply for this job" on details page
- `adzuna_button_2_clicked` - Clicked "Apply Now" on land page
- `adzuna_button_3_clicked` - Clicked "Apply Now" on DeJobs page
- `adzuna_flow_active` - Currently in Adzuna flow
- `adzuna_flow_completed` - Successfully completed Adzuna flow
- `adzuna_preliminary_actions` - Saved list of 3 preliminary button clicks
- `on_dejobs_intermediate` - Currently on DeJobs intermediate page

### 3. **Robust Detection**
Each page has multiple fallback selectors to handle:
- Different button styles and classes
- Different HTML structures
- Changes in Adzuna's website structure

### 4. **Prevention of False Positives**
- Auth detector skips Adzuna and DeJobs pages
- Generic job page bypass prevents auth detection on application pages
- URL-based detection ensures correct flow progression

---

## 🔍 Detection Confidence Scores

| Method | Confidence | When Used |
|--------|-----------|-----------|
| **Adzuna details page** | 0.95 | URLs containing "adzuna.com/details/" |
| **Adzuna land page** | 0.95 | URLs containing "adzuna.com/land/ad/" |
| **DeJobs page** | 0.95 | URLs containing "dejobs.org/*/job/" |
| Primary patterns | 0.95 | Exact "Apply Now" text matches |
| Secondary patterns | 0.80 | "Apply" text or aria-label |
| Tertiary patterns | 0.60 | Class-based matches |
| AI fallback | 0.50 | When all patterns fail |

---

## 📊 Flow State Transitions

```
START
  ↓
ai_guided_navigation (detects Adzuna details page)
  ↓
click_apply (BUTTON 1: "Apply for this job")
  ↓
ai_guided_navigation (detects Adzuna land page)
  ↓
click_apply (BUTTON 2: "Apply Now" to DeJobs)
  ↓
ai_guided_navigation (detects DeJobs page)
  ↓
click_apply (BUTTON 3: "Apply Now" to actual app)
  ↓
[New tab opens with actual application]
  ↓
ai_guided_navigation (clears action sequence, starts fresh)
  ↓
fill_form (normal form filling begins)
```

---

## 🧪 Testing

To test the Adzuna flow:

1. **Find an Adzuna job posting:**
   ```
   https://www.adzuna.com/details/XXXXXXX?se=...&utm_medium=api&utm_source=...&v=...
   ```

2. **Start the job application agent** with this URL

3. **Expected behavior:**
   - ✅ Email popup closes automatically
   - ✅ "Apply for this job" button clicked (BUTTON 1)
   - ✅ Navigates to Adzuna land page
   - ✅ "Apply Now" button clicked (BUTTON 2)
   - ✅ Navigates to DeJobs page
   - ✅ "Apply Now" button clicked (BUTTON 3)
   - ✅ New tab opens with actual application
   - ✅ Action sequence cleared
   - ✅ Form filling begins normally

4. **Check logs for:**
   - "🔵 BUTTON 1: Adzuna details page"
   - "🔵 BUTTON 2: Adzuna land page"
   - "🔵 BUTTON 3: DeJobs intermediate page"
   - "✅ Reached actual application from Adzuna 3-button flow!"
   - "🧹 Cleared preliminary action sequence - saved 3 Adzuna button clicks"
   - "🆕 Starting fresh on actual application page"

---

## 🔧 Configuration

No additional configuration is required. The flow is automatically detected based on URL patterns.

### Customization Options

If you need to customize the selectors or timeouts:

**Email popup timeout:** Change `await asyncio.sleep(2)` in `_handle_adzuna_details_page()`

**Page load timeout:** Change `await asyncio.sleep(1)` in `_handle_adzuna_land_page()`

**Add custom selectors:** Append to the selector lists in each handler method

---

## 🐛 Troubleshooting

### Issue: Email popup doesn't close
**Solution:** The agent continues even if popup close fails. Check if popup selector has changed.

### Issue: "Apply for this job" button not found
**Solution:** Check logs for which selectors were tried. Add new selector if needed.

### Issue: Action sequence not cleared
**Solution:** Verify URL detection logic. Ensure application URL doesn't contain "adzuna.com" or "dejobs.org".

### Issue: Auth detector interferes
**Solution:** Verify auth_page_detector.py has Adzuna/DeJobs bypasses (lines 61-81).

---

## 📝 Modified Files

1. **`Agents/components/detectors/apply_detector.py`**
   - Added `_handle_adzuna_details_page()` method
   - Added `_handle_adzuna_land_page()` method
   - Updated `detect()` method with URL detection

2. **`Agents/job_application_agent.py`**
   - Updated `_state_click_apply()` with comprehensive flow tracking
   - Added action sequence clearing logic
   - Added state flags for flow progression

3. **`Agents/components/detectors/auth_page_detector.py`**
   - Added Adzuna bypass
   - Added DeJobs bypass
   - Added generic job page bypass

---

## 🔧 Fixes Applied (Latest Update)

### Issue 1: Unknown State 'validate_apply' Error
**Problem:** After clicking Button 1 and resolving cookie popup on DeJobs, the agent tried to transition to non-existent `validate_apply` state.

**Solution:** Updated `_state_resolve_blocker()` in `job_application_agent.py` (line 1104-1118):
- Removed reference to non-existent `validate_apply` state
- After resolving post-apply popups, now returns to `ai_guided_navigation`
- Added special handling for Adzuna/DeJobs flow continuation

### Issue 2: DeJobs Detection Not Triggered
**Problem:** The `detect()` method in `apply_detector.py` only checked for Adzuna URLs, not DeJobs.

**Solution:** Added DeJobs detection after Adzuna checks (line 66-69):
```python
# Check for DeJobs intermediate page (comes after Adzuna or standalone)
if "dejobs.org" in current_url and "/job/" in current_url:
    logger.info("🔍 Detected DeJobs intermediate page - clicking 'Apply Now' button")
    return await self._handle_dejobs_page()
```

### Issue 3: Improved DeJobs Button Detection
**Problem:** DeJobs handler only had one selector which might fail.

**Solution:** Added multiple fallback selectors in `_handle_dejobs_page()` (line 250-256):
- `a[href*="rr.jobsyn.org"]:has-text("Apply Now")` - Most specific
- `a[href*="rr.jobsyn.org"]` - By redirect URL
- `a.bg-button:has-text("Apply Now")` - By class and text
- `a[class*="bg-button"]:has-text("Apply Now")` - Flexible class match
- `a:has-text("Apply Now")` - Generic fallback

### Issue 4: Flow Tracking for 2-Button Route
**Problem:** Original implementation assumed 3-button flow, but Adzuna can skip directly to DeJobs.

**Solution:** Updated flow tracking in `_state_click_apply()` (line 1274-1330):
- Detects whether route goes through land page or directly to DeJobs
- Clears 2 or 3 actions depending on route taken
- Logs correct button number: "BUTTON 2 (direct from details)" or "BUTTON 3"

---

## ✅ Success Criteria

- [x] Detects Adzuna details page by URL pattern
- [x] Closes email alert popup automatically
- [x] Clicks "Apply for this job" button
- [x] Detects Adzuna land page by URL pattern
- [x] Clicks "Apply Now" button to DeJobs
- [x] Detects DeJobs intermediate page by URL pattern
- [x] Clicks "Apply Now" button to actual application
- [x] Tracks flow progression with state flags
- [x] Clears action sequence after all 3 buttons
- [x] Preserves all actions in ActionRecorder
- [x] Prevents auth detector false positives
- [x] Handles new tab opening for actual application
- [x] Starts normal form filling after flow completes

---

## 🎉 Result

The agent now seamlessly handles Adzuna's 3-button flow:
- ✅ All preliminary buttons clicked automatically
- ✅ Action sequence cleared for clean form filling
- ✅ All actions preserved in recorder for debugging
- ✅ No false positive auth detections
- ✅ Smooth transition to actual application form

