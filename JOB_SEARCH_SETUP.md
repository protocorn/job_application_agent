# Multi-Source Job Search Setup Guide

## Overview

Your job search system now aggregates jobs from **4 different sources** and ranks them by relevance to the user's profile. This provides much better job coverage and quality compared to using a single API.

## Architecture

### 1. **Job API Adapters** (`Agents/job_api_adapters.py`)
   - Unified interface for multiple job APIs
   - Normalizes data from different sources to a standard format
   - Supports: JSearch, Adzuna, Active Jobs DB, Google Jobs (SerpAPI)

### 2. **Relevance Scoring** (`Agents/job_relevance_scorer.py`)
   - Keyword-based matching (no expensive LLM calls)
   - Scores jobs 0-100 based on:
     - Title keyword match (25 points)
     - Description keyword match (20 points)
     - Experience level match (15 points)
     - Salary match (15 points)
     - Location match (10 points)
     - Job type match (10 points)
     - Recency bonus (5 points)

### 3. **Multi-Source Discovery Agent** (`Agents/multi_source_job_discovery_agent.py`)
   - Searches all APIs in parallel
   - Deduplicates results
   - Ranks by relevance
   - Saves to PostgreSQL with scores

### 4. **Enhanced Database Schema** (`database_config.py`)
   - User profile now includes:
     - `open_to_remote` - Remote work preference
     - `open_to_anywhere` - Work anywhere in country
     - `preferred_cities` - Specific cities
     - `preferred_states` - Specific states
     - `minimum_salary`, `maximum_salary` - Salary expectations
     - `years_of_experience` - Total experience
     - `desired_job_types` - Full-time, part-time, contract, etc.
     - `desired_experience_levels` - Entry, mid, senior, etc.
   - Job listings now include:
     - `job_type`, `experience_level`, `is_remote`
     - `salary_min`, `salary_max`, `salary_currency`
     - `relevance_score` (0-100)
     - `user_id` (for personalized job lists)

---

## Required API Keys

Add these to your `.env` file:

### 1. **JSearch (RapidAPI)** - RECOMMENDED ⭐
   - **Best quality data** with comprehensive job information
   - **Free tier**: 150 requests/month
   - **Paid tier**: Starting at $10/month for 1,500 requests
   - **Get it**: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
   - **Add to .env**:
     ```
     JSEARCH_RAPIDAPI_KEY=your_jsearch_key_here
     ```

### 2. **Adzuna** - FREE TIER AVAILABLE ⭐
   - **Free tier**: 500 calls/month (very generous!)
   - **Great coverage** for US, UK, and other countries
   - **Get it**: https://developer.adzuna.com/signup
   - **Add to .env**:
     ```
     ADZUNA_APP_ID=your_app_id_here
     ADZUNA_APP_KEY=your_app_key_here
     ```

### 3. **Active Jobs DB (RapidAPI)** - ALREADY CONFIGURED
   - You already have this (`RAPIDAPI_KEY` in your env)
   - **IMPORTANT**: Move the hardcoded key from `job_discovery_agent.py:178` to `.env`!
   - **Add to .env**:
     ```
     RAPIDAPI_KEY=5da97ff77emshe8c06807a5985e3p158ad3jsnbab5006c61bd
     ```

### 4. **Google Jobs (SerpAPI)** - OPTIONAL
   - Aggregates from Google's job listings
   - **Free tier**: 100 searches/month
   - **Paid tier**: $50/month for 5,000 searches
   - **Get it**: https://serpapi.com/users/sign_up
   - **Add to .env**:
     ```
     SERPAPI_KEY=your_serpapi_key_here
     ```

---

## Recommended Configuration

### **Minimum Setup** (Free)
```env
# Free tier - Adzuna only (500 calls/month)
ADZUNA_APP_ID=your_app_id
ADZUNA_APP_KEY=your_app_key
```

### **Basic Setup** (Free + Existing)
```env
# Adzuna (free) + Active Jobs DB (existing)
ADZUNA_APP_ID=your_app_id
ADZUNA_APP_KEY=your_app_key
RAPIDAPI_KEY=your_existing_key
```

### **Recommended Setup** ($10/month)
```env
# JSearch + Adzuna + Active Jobs DB
JSEARCH_RAPIDAPI_KEY=your_jsearch_key
ADZUNA_APP_ID=your_app_id
ADZUNA_APP_KEY=your_app_key
RAPIDAPI_KEY=your_existing_key
```

### **Premium Setup** ($60/month)
```env
# All sources
JSEARCH_RAPIDAPI_KEY=your_jsearch_key
ADZUNA_APP_ID=your_app_id
ADZUNA_APP_KEY=your_app_key
RAPIDAPI_KEY=your_existing_key
SERPAPI_KEY=your_serpapi_key
```

---

## About Google Jobs / SerpAPI

### **Is Google Jobs Worth It?**

**Pros:**
- Aggregates jobs from many sources (Indeed, LinkedIn, company websites)
- Very comprehensive coverage
- Clean, structured data
- Good for location-based searches

**Cons:**
- More expensive ($50/month for meaningful volume)
- The free tier (100 searches) runs out quickly
- JSearch + Adzuna already provide excellent coverage
- Diminishing returns - you'll get many duplicate jobs

### **My Recommendation:**
**Start with JSearch + Adzuna** (total cost: $10/month + free). These two sources will give you:
- 1,500+ jobs per month (JSearch paid)
- 500 additional jobs (Adzuna free)
- Excellent coverage across major job boards
- High-quality, structured data

Only add Google Jobs if:
- You need extremely comprehensive coverage
- You're doing high-volume searches (multiple searches per day)
- Budget is not a concern

---

## Database Migration

After adding the new API keys, you need to update your database schema:

```bash
# Run this to add new columns to existing tables
python database_config.py
```

This will add:
- New profile fields (location preferences, salary expectations, etc.)
- New job listing fields (relevance score, job type, experience level, etc.)

---

## Testing the System

1. **Install new dependency:**
   ```bash
   pip install python-dateutil
   ```

2. **Set up your API keys** in `.env`

3. **Update database schema:**
   ```bash
   python database_config.py
   ```

4. **Test the multi-source agent:**
   ```bash
   cd Agents
   python multi_source_job_discovery_agent.py
   ```

5. **Check the results:**
   - Should see jobs from multiple sources
   - Each job should have a relevance score (0-100)
   - Jobs should be sorted by relevance

---

## API Endpoint Changes

### **Search Jobs** (Updated)
```http
POST /api/search-jobs
Authorization: Bearer <token>
Content-Type: application/json

{
  "min_relevance_score": 30  // Optional, default: 30
}

Response:
{
  "jobs": [...],
  "total_found": 45,
  "sources": {
    "JSearch": 15,
    "Adzuna": 20,
    "ActiveJobsDB": 10,
    "GoogleJobs": 0
  },
  "average_score": 67.5,
  "saved_count": 45,
  "updated_count": 0,
  "success": true,
  "message": "Found 45 jobs from multiple sources with avg score 67.5"
}
```

### **Get Job Listings** (Updated)
```http
GET /api/job-listings?sort_by=relevance&limit=20&offset=0
Authorization: Bearer <token>

Response:
{
  "jobs": [...],  // Jobs sorted by relevance score
  "total_count": 45,
  "limit": 20,
  "offset": 0,
  "sort_by": "relevance",  // or "date" or "salary"
  "success": true
}
```

---

## Performance & Cost Estimates

### With Minimum Setup (Free)
- **Cost**: $0/month
- **Job Sources**: Adzuna only
- **Jobs per search**: ~10-20
- **Monthly searches**: 500 / ~10 = ~50 searches
- **Good for**: Testing, low-volume usage

### With Basic Setup (Free + Existing)
- **Cost**: $0/month
- **Job Sources**: Adzuna + Active Jobs DB
- **Jobs per search**: ~20-30
- **Good for**: Regular usage, small user base

### With Recommended Setup ($10/month)
- **Cost**: $10/month
- **Job Sources**: JSearch + Adzuna + Active Jobs DB
- **Jobs per search**: ~40-60
- **Monthly searches**: Effectively unlimited for small-medium usage
- **Good for**: Production use, growing user base

---

## Relevance Scoring Examples

### High Score (80-100): Excellent Match
- Title matches user's recent job titles
- 10+ matching skills in description
- Experience level matches perfectly
- Salary meets expectations
- Location matches preferences
- Job type matches (e.g., full-time)
- Posted within last week

### Medium Score (50-79): Good Match
- Some title keyword overlap
- 5-9 matching skills
- Experience level within 1 level of user
- Salary partially meets expectations
- Location acceptable (remote or preferred state)
- Posted within last month

### Low Score (30-49): Possible Match
- Few matching keywords
- 2-4 matching skills
- Experience level within 2 levels
- Salary below expectations or not specified
- Location not ideal but acceptable
- Older posting

### Filtered Out (<30): Poor Match
- Minimal keyword overlap
- Wrong experience level
- Wrong location and not remote
- Salary far below expectations

---

## Troubleshooting

### "No jobs found"
1. Check API keys are correctly set in `.env`
2. Check user profile has sufficient data (skills, work experience)
3. Try lowering `min_relevance_score` (e.g., to 20)
4. Check API rate limits haven't been exceeded

### "Error searching X API"
- Check API key is correct
- Check API rate limits
- Check network connection
- The system will continue with other sources

### Database errors
- Run `python database_config.py` to create/update tables
- Check PostgreSQL is running
- Check database credentials in `.env`

---

## Future Enhancements

Possible additions:
1. **LinkedIn Jobs API** (requires LinkedIn API access)
2. **Indeed API** (requires Indeed publisher account)
3. **Glassdoor API** (for company reviews + salary data)
4. **Remote-specific APIs** (We Work Remotely, Remote.co)
5. **AI-enhanced scoring** (optional LLM refinement for top matches)

---

## Summary

✅ **What we built:**
- Multi-source job aggregation (4 APIs)
- Intelligent relevance scoring (0-100)
- Enhanced profile with job preferences
- Deduplication across sources
- Sorting by relevance, date, or salary

✅ **Required API Keys:**
- Adzuna (free) - **RECOMMENDED**
- JSearch ($10/month) - **OPTIONAL but recommended**
- Active Jobs DB (you already have this)
- SerpAPI/Google Jobs ($50/month) - **OPTIONAL, not necessary**

✅ **Cost estimate:**
- Minimum: **$0/month** (Adzuna free tier)
- Recommended: **$10/month** (JSearch + Adzuna)
- Premium: **$60/month** (All sources)

✅ **Next steps:**
1. Sign up for Adzuna (free): https://developer.adzuna.com/signup
2. Optionally sign up for JSearch: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
3. Add API keys to `.env`
4. Run `python database_config.py` to update schema
5. Test the system!
