# Project Selection - Current Status & Integration Plan

## Current Behavior
When systematic tailoring runs, it identifies low-relevance projects but doesn't replace them:

```
🔧 Processing section: PROJECTS (Priority 3)
      Found 3 project(s)
      • Mimikree - Personalized AI Assistant | mimikree.co... (relevance: 0/100)
      • AI Document Generator – Capital Area Food Bank AI ... (relevance: 0/100)
      • Local Document Search Engine    March 2025 - May 2025... (relevance: 0/100)
      ⚠️  Found 3 low-relevance project(s) (< 20/100)
      💡 Suggestion: Consider replacing with more relevant projects from your experience
   ✓ Generated 0 replacements  # ← Does nothing!
```

## Why It Doesn't Replace Projects Automatically

The current implementation (lines 969-982 in `systematic_tailoring_complete.py`) only flags low-relevance projects but doesn't replace them because:

1. **Architecture Decision**: Project discovery and selection was designed as a **user-interactive workflow** (via frontend UI)
2. **User Control**: Replacing projects automatically without user approval could remove important work the user wants to showcase
3. **Complex Dependencies**: Project selection requires:
   - Querying Mimikree API for discovery
   - Generating tailored bullets for new projects
   - User reviewing and approving selections
   - Updating project database

## What We've Built (Ready to Use)

### Backend Infrastructure
1. **Project Relevance Engine** (`Agents/project_selection/relevance_engine.py`)
   - `calculate_overall_relevance()` - Multi-factor scoring
   - `recommend_project_swaps()` - Suggests replacements

2. **Mimikree Project Discovery** (`Agents/project_selection/mimikree_project_discovery.py`)
   - `discover_projects()` - Finds additional projects from Mimikree
   - `generate_discovery_questions()` - Intelligent questioning

3. **Project Bullet Generator** (`Agents/bullet_generation/project_bullet_generator.py`)
   - `generate_bullets()` - Creates tailored 2-3 line bullets

4. **API Endpoints** (`server/api_server.py`, lines 1935-2349):
   - `POST /api/tailoring/analyze-projects` - Analyzes relevance + discovers from Mimikree
   - `POST /api/tailoring/generate-project-bullets` - Generates bullets on demand
   - `POST /api/projects/save-discovered` - Saves discovered projects to database

### Frontend Components
1. **ProjectSelector.js** - Interactive UI for selecting projects during tailoring
2. **ProjectManager.js** - CRUD interface for managing project library

## Integration Options

### Option 1: Manual Workflow (Recommended for MVP)
**Flow:**
1. User starts tailoring from frontend
2. Before tailoring runs, frontend calls `/api/tailoring/analyze-projects`
3. Shows ProjectSelector UI with:
   - Current projects with relevance scores
   - Swap recommendations
   - Discovered projects from Mimikree
4. User selects which projects to include
5. Frontend calls `/api/tailoring/generate-project-bullets` for selected projects
6. Tailoring proceeds with user-approved project selection

**Pros:**
- User maintains control
- Can review/edit generated bullets
- Prevents unwanted project removal

**Cons:**
- Extra step in workflow
- Not fully automatic

### Option 2: Automatic with Confirmation
**Flow:**
1. Systematic tailoring detects low-relevance projects
2. Automatically calls Mimikree discovery API
3. Generates swap recommendations
4. **Pauses and asks user for confirmation** before applying
5. Applies approved swaps

**Pros:**
- More automated
- Still has user oversight

**Cons:**
- Requires building confirmation dialog
- More complex state management

### Option 3: Fully Automatic (Not Recommended)
**Flow:**
1. Systematic tailoring detects low-relevance projects
2. Automatically discovers and swaps projects
3. No user input

**Pros:**
- Completely hands-off

**Cons:**
- User loses control over their resume
- Could remove projects user wants
- Risky for professional documents

## Recommended Next Steps

### Immediate (Quick Win)
1. **Keep current behavior** (flag but don't replace) in automatic mode
2. **Add note to output** suggesting user review project selection in UI:
   ```python
   if low_relevance_projects:
       print(f"      ⚠️  Found {len(low_relevance_projects)} low-relevance project(s)")
       print(f"      💡 TIP: Use the Project Selector UI to find better matches from your Mimikree profile")
       print(f"           Visit: /tailor-resume → Project Selection step")
   ```

### Short-term (Best UX)
1. **Integrate ProjectSelector into tailoring workflow**:
   - Add new step in `TailorResumePage.js` BEFORE systematic tailoring runs
   - Show ProjectSelector component with analysis results
   - Allow user to approve/modify project selection
   - Pass selected projects to tailoring agent

2. **Wire up frontend routes**:
   ```javascript
   // In TailorResumePage.js
   const analyzeProjects = async (jobDescription, currentProjects) => {
       const response = await fetch('/api/tailoring/analyze-projects', {
           method: 'POST',
           body: JSON.stringify({ job_description: jobDescription, current_projects: currentProjects })
       });
       return response.json(); // { recommendations, discovered_projects }
   };
   ```

### Long-term (Full Integration)
1. **Project usage tracking** - Analytics on which projects get selected for which jobs
2. **Smart defaults** - ML model learns user preferences for project selection
3. **A/B testing** - Track success rates (interviews/offers) by project selection strategy

## Code Locations

### Where low-relevance projects are detected:
[`Agents/systematic_tailoring_complete.py:969-982`](Agents/systematic_tailoring_complete.py#L969-L982)

```python
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
```

### Where to integrate automatic discovery (if desired):
Same location - replace the comment with:
```python
if low_relevance_projects:
    # TODO: Optionally trigger Mimikree discovery here
    # from project_selection.mimikree_project_discovery import MimikreeProjectDiscovery
    # discovery = MimikreeProjectDiscovery(gemini_api_key)
    # discovered = discovery.discover_projects(mimikree_client, keywords, job_desc, projects)
    pass
```

## Status
- **Skills Section**: ✅ FIXED - Now intelligently adds/removes based on Mimikree
- **Project Selection**: ⏳ INFRASTRUCTURE READY - Awaiting frontend integration decision

## Decision Needed
Which integration option do you prefer?
1. Manual (user-driven via ProjectSelector UI) - safest, gives user control
2. Automatic with confirmation - balanced approach
3. Fully automatic - fastest but risky
