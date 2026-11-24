# European job search worker

The daily worker reads a private candidate CV, screens company career feeds and the Arbeitnow job board, records decisions in SQLite and writes daily and weekly reports. The public repository contains only reusable code. Candidate documents, evidence, application records and reports belong in a separate private repository.

## Cloud operation

The scheduled workflow uses a standard public Ubuntu runner at 13:17 UTC. In Actions, choose **Job search → Run workflow** for an additional run. The scheduler can be delayed. GitHub can disable scheduled workflows in public repositories after 60 days without repository activity. The Actions page shows whether the schedule is enabled.

The private repository needs a writable, repository-specific deploy key. Store its private half in the worker's Actions secret `STATE_DEPLOY_KEY` and the private repository name in `STATE_REPOSITORY`. The worker persists progress before sending any application and again after a receipt. If delivery is uncertain it holds that application for reconciliation. Read the private reports for failed sources. A successful workflow means the worker finished, and individual sources can still fail.

## Private candidate settings

The private repository contains `cv.docx`, `settings.json`, generated reports, application packets and `history.sqlite`. Use the preferred display name in `name` and put exact answers to supported form questions in `answers`. Unknown mandatory answers stop a form. Keep `application_ready` false while the CV contains unfinished claims. Input documents should have ordinary text paragraphs and headings. The current reader also ingests text from tables.

Set `report_to`, `smtp_host` and `smtp_user` in private settings. Set `SMTP_PASSWORD` as an Actions secret. Outgoing mail uses TLS on port 465. Pending reports are delivered on a later run when the sender becomes available. No inbox connection is required for discovery. Outcomes received elsewhere can be recorded using `python agent.py --outcome JOB_ID interview`, substituting `rejection` or `offer` as appropriate, in a synchronized private workspace.

## Application coverage

The worker distinguishes signs of role-level sponsorship from confirmed permission to apply from overseas. It checks the UK sponsor register for UK roles. A register entry alone does not establish individual visa eligibility. European checks currently use job-specific employer evidence. A clean CV is required before sending. Employer-board postings are fetched again before dispatch.

Email dispatch requires an explicit CV-by-email invitation on the employer's live posting, a matching employer domain and an authorized sender. Browser dispatch requires a supported employer form. Configure `routes` by board (for example `gh/example`) with `form_origin` (exact hostname), `employer_automation_policy` (reviewed evidence) and `valid_until` (ISO date). Review each site's policy and form before adding a browser route. The worker leaves unconfirmed openings in the report. It cannot handle every country, role, language, portal or form.

The optional local embedding model ranks exact CV passages. If the encoder or model download is unavailable, term matching ranks those passages. Paid model APIs are unnecessary. The search classifier uses English terms, so multilingual listings can be missed.

## Local checks

Use Python 3.13, install `requirements.txt`, and run `python -m unittest -v` in the code directory. On Windows, install `tzdata` if a timezone database is unavailable. Set `STATE_DIR` to the private workspace and run `python agent.py` for a search. Set `application_ready` false in a separate test workspace to exercise discovery without submitting applications. Production runs are serialized by GitHub Actions.
