# Configuring your facts, answers and settings

Three files describe you and how the bot behaves. All three are git-ignored, and each has a tracked example:

| File | Example | What it is |
|---|---|---|
| `cv/facts.yaml` | `cv/facts.example.yaml` | Your CV as data, plus search and scoring preferences. The single source of truth. |
| `data/answers.yaml` | `data/answers.example.yaml` | Answers to recurring form questions. It grows as you answer parked questions. |
| `data/settings.json` | `data/settings.example.json` | Knobs: score bars, caps, AI routing, CV PDF path, outreach. |

`JOBHUNT_FACTS` and `JOBHUNT_ANSWERS` point to other files if you set them (the tests use this to load an invented person).

**The golden rule: everything in these files can be submitted on a real application in your name.** Write only what is
true and what you could defend in an interview. Leave a value out rather than guess it: a missing value parks the job as
`needs_you` with the exact question, and nothing is ever invented in its place.

## facts.yaml

### personal

| Field | Used for |
|---|---|
| `name` | Full-name fields, the CV header, the CV file name (`<Name>_Resume.pdf`). |
| `first_name`, `last_name` | First and last name fields. If missing, they are split from `name`. |
| `email` | Every portal account login, the mailbox the bot reads, and outreach `From`. |
| `phone` | Phone fields (digits only, without the country code) and the CV. |
| `phone_country_code` | Country phone code fields, for example `+91`. |
| `location`, `city` | CV header and the default home-location filter. |
| `linkedin` | CV header, and LinkedIn URL fields if `answers.yaml linkedin` is absent. |
| `github` | Website, portfolio and GitHub fields. Leave it empty if you have none; those boxes then stay empty. |

### CV content

- `headline`: your CV headline (fallback). The tailored headline is `headline_title` (default: your latest job title) +
  the three of your tools the posting names most + optional `headline_tags` (a list).
- `summary`: the CV summary. Optional `family_summaries: {role_family: text}` gives alternative summaries per kind of job
  (for example `data_bi`, `risk_reg`, `finance`, `etl_sas`, `returnship`, or your own families). Write them only from
  claims already in this file.
- `experience`: list of `title`, `company`, `location`, `start`, `end` (`Mon YYYY`, for example `Mar 2024`), `bullets`.
  The most recent job comes first. The scorer's "real total" comes from these dates (`rank/score.her_total_years`; an end
  month counts in full, and an unreadable date or `Present` is not counted, so the total can only come out lower, never
  inflated). Forms use `answers.yaml total_experience_years`.
- `projects` (optional): `name`, `tech`, `year`, `link` (may be empty), `bullets`.
- `certifications` (optional): list of strings.
- `skills`: categories of lists, for example `Tools: [SQL, Tableau]`. Only skills listed here ever appear on a CV. The
  first category (or one named `Tools`) feeds the headline.
- `education`: list of `degree`, `institution`, `year` (graduation year), `start_year`, `start` and `end` (month + year
  exactly as on your certificate), `field`. The first entry is treated as your highest degree. When a form asks for a
  month or day you have not recorded, the job is parked; it is never guessed.

### not_claimable

```yaml
not_claimable: [Python, Snowflake, AWS]
```

Skills you do **not** have. They are never claimed on a form or CV. When a posting requires one, its fit score is capped
below your bar. A question like "years of experience with X" for one of these is answered 0 or No, or parked. Built-in
"lacked" skills (Python, Java, Airflow, ...) are also treated as gaps unless you list them under `skills`.

### preferences (search, filter, scoring)

| Field | Default | Meaning |
|---|---|---|
| `home_location_regex` | your `city` | Regular expression (case-insensitive) of locations you accept, for example `bengaluru\|bangalore\|karnataka`. Remote jobs are judged separately (see Regional assumptions below). |
| `candidate_profile` | `summary` | One paragraph the AI scorer reads about you. Facts only. |
| `eligible_roles` | generic | The roles you are eligible for. The scorer gives these 55+ unless a mandatory skill is missing. |
| `unrelated_fields` | generic | Fields scored below 35. |
| `missing_qualifications` | none | Licences you do not hold (for example "CA, CFA charter"). |
| `max_stated_min_years` | 0 (off) | Skip postings whose stated minimum experience is N+ years. Returnships use at least 7. |
| `python_level` | not stated | Stated only if you set it, for example "basic exposure only". |
| `languages` | none | Languages you speak (used for language rows on forms). Proficiency levels are never invented. |
| `search_queries` | built-in analyst list | **The kinds of job you want**: titles to search for on boards, LinkedIn, Naukri and Workday. |
| `include_returnships` | true | Add returnship and contract searches to `search_queries`. |
| `job_families` | built-in | `{family_name: regex}`: extra role families a job title must match to be kept. |
| `replace_job_families` | false | `true` replaces the built-in families (data / BI, risk / regulatory, finance, ETL / SAS) with yours. |
| `title_exclude_regex` | built-in | Regular expression for titles to drop. The built-in one drops managers, directors, engineers, developers, data scientists, interns and similar roles. Replace it if you want those. |

Example for a different kind of job:

```yaml
preferences:
  home_location_regex: "pune|pimpri|hinjewadi"
  search_queries: ["QA Engineer", "Test Automation Engineer", "SDET"]
  replace_job_families: true
  job_families:
    qa: "\\bqa\\b|quality assurance|test (automation|engineer)|sdet|tester"
  title_exclude_regex: "\\b(intern|director|vice president|head|principal|sales)\\b"
```

### outreach (optional)

`subject`, `pitch` and `ask` are used for honest emails to startups that publish a hiring address (`{company}` is
replaced). With no `pitch`, outreach is off.

## answers.yaml

Keys are short names or **slugs of a question's text**. When you answer a parked question on the dashboard, its slug is
saved here and reused for every identical question, at every company. The keys the built-in rules read:

| Group | Keys |
|---|---|
| Experience and pay | `total_experience_years`, `relevant_experience_years`, `notice_period`, `has_notice_period`, `available_to_join`, `available_immediately`, `currently_employed`, `current_ctc_lpa`, `expected_ctc_lpa`, `last_ctc_lpa`, `last_ctc_fixed_lpa`, `last_ctc_variable_lpa`, `expected_monthly`, `current_monthly`, `last_working_day` (`DD Mon YYYY`), `reason_for_leaving_last_job`, `career_gap_reason` |
| Current role | `current_employer_answer` (what to put in a "current employer" box), `last_employer`, `last_title` |
| Work rights and place | `work_authorization_home`, `requires_sponsorship`, `authorized_in_other_countries` (set true only if it is true), `willing_to_relocate`, `open_to_hybrid`, `nationality`, `citizenship_status`, `other_citizenship`, `current_location`, `current_city`, `current_state`, `current_country`, `city_aliases`, `address`, `address_line_2`, `area`, `pincode` |
| Personal | `date_of_birth` (`DD/MM/YYYY`), `gender`, `prefix`, `phone_device_type`, `languages`, `linkedin`, `github`, `national_id_number` (only if you choose to give it) |
| Education | `highest_degree`, `degree_level_option`, `holds_degree`, `institution`, `school_or_university`, `field_of_study`, `graduation_year` |
| Declarations | `over_18`, `knows_no_insider` (you know no director/employee: referral and relative questions get No), `no_compliance_flags` (no criminal record, sanctions, conflicts, non-competes, PEP relatives, disciplinary history), `how_did_you_hear` |
| Skill years | `skill_years: {regex: years}` and `skill_years_confirmed: true`. Skill-specific year questions are answered only from this table, and only once you confirm it. |

Rules the code enforces whatever you write:

- Scores and grades (GPA, percentage) are never guessed. They are left empty.
- Diversity questions (race, disability, veteran, gender identity) are answered "decline to self-identify" when that option exists.
- Marketing and newsletter opt-ins are never ticked. Consent to process your application is.
- A place (city, country) is never the answer to a yes/no question, and a work-authorisation question about a country other
  than `current_country` is answered from the facts (not authorised there; sponsorship needed), unless you set
  `authorized_in_other_countries: true`.
- A question that names one of your former employers ("have you worked for X before?") is never answered with a blanket No.
- Self-ratings ("rate yourself out of 10"), commercial preferences (contract, third-party payroll, shifts) and ID numbers
  always come to you.

**Common answers / the answer cache.** Besides `answers.yaml`, the bot keeps a cache of non-personal answers in the
database (`answers_cache`, for example consent and "how did you hear" choices) so that one answer is reused across
companies. It refuses to cache junk or guessed values (`core/cache.py`). Facts about you always come from the two files.

## settings.json

| Key | Default | Meaning |
|---|---|---|
| `min_score` | 50 | Minimum AI fit score (0-100) for a job to be applied to. Lower means more applications, and more of them a stretch. |
| `naukri_min_score` | 45 | The same bar for Naukri jobs. |
| `top_min_score` | 55 | A stricter bar for top-tier companies (banks, MNCs, big tech: `tier` in `discover/companies_*.yaml`). |
| `returnship_min_score` | 30 | Bar for returnship programmes. |
| `daily_goal` | 100 | The dashboard's daily target line. |
| `naukri_daily_cap` | 12 | Naukri applications per day. |
| `company_daily_cap` | 3 | Applications per company per day. |
| `li_max_applicants` | 10 | LinkedIn jobs only when the page shows fewer applicants than this (0 turns the rule off). |
| `skip_companies` | [] | Lower-case words. A company whose name contains one is never applied to. |
| `llm_primary` | - | `"backup"` uses `llm_providers.json` first and Claude last. |
| `claude_for_bot` | true | `false` stops the bot from using the Claude CLI at all. |
| `base_cv_pdf` | `data/resumes/_latest/resume.pdf` | Your own CV PDF (job-board profile uploads, outreach). |
| `accounts_mirror` | false | Also keep portal passwords (and their previous passwords, for retries) base64-encoded in `data/accounts.json`. Not encryption. |
| `outreach_send` | false | Let the daily outreach job SEND the emails it drafts. Off means drafts only. |
| `browser_locale`, `browser_timezone` | `en-IN`, `Asia/Kolkata` | Locale and timezone of the bot's Chrome profile. |

The fit bars are read at queue time, so changing one needs no rescoring.

## Regional assumptions

These parts assume a search based in India and are not yet configurable:

- Discovery searches pass "Bengaluru" or "Bangalore" as the location on several company APIs (`discover/ats_misc.py`,
  `ats_oracle.py`, `ats_custom.py`, `aggregators.py`), and LinkedIn uses a Bengaluru geo id (`discover/linkedin.py`).
- `rank/filter.py` knows Indian cities and states (`OTHER_CITY`, `OTHER_STATE`) and treats remote jobs naming India, APAC or
  "worldwide" as acceptable, and remote jobs naming other countries as not.
- Pay fields are understood in lakh per annum (LPA) and rupees.
- Automatic correction of country and phone-code fields works only when `current_country` is India. For other countries
  these fields are filled from your answers but not auto-corrected.

Contributions that make these configurable are welcome (see [CONTRIBUTING.md](CONTRIBUTING.md)).
