# Organization-Approved Support & Governance Policies

This document establishes the official policies for triage priority, escalation, customer communications, privacy, and data retention.

---

## 1. Priority Policy: Money & Critical Incident Triage

All incoming support requests are triaged into two operational priority classes: **urgent** and **normal**.

### Urgent Priority Criteria
A ticket **must** be classified as `urgent` under any of the following conditions:
1. **Financial & Money Impact (Mandatory Rule)**:
   - Any ticket categorized under `billing`.
   - Any ticket containing terms regarding payments, refunds, charges, invoices, credit card processing, overcharges, subscription billing disputes, banking, or monetary transactions.
   - *Rationale*: Direct financial discrepancies directly affect customer trust and company revenue, demanding accelerated resolution.
2. **Security Incidents**:
   - Compromised credentials, unauthorized logins, data breaches, vulnerability discoveries, or active penetration attempts.
3. **Severe Operational Downtime**:
   - System outages, critical API failures, or issues impacting multiple customers simultaneously.

### Normal Priority Criteria
- General technical bug reports, standard feature inquiries, account preference updates, and general onboarding questions.

---

## 2. Escalation Policy & SLAs

| Category | Priority | Initial Response SLA | Escalation Target |
| :--- | :--- | :--- | :--- |
| **Billing / Money** | Urgent | **< 15 minutes** | Dedicated Billing Lead & On-call Operations |
| **Security** | Urgent | **< 15 minutes** | Security Incident Response Team (SIRT) |
| **Outage** | Urgent | **< 15 minutes** | Site Reliability Engineering (SRE) Slack |
| **Technical** | Normal | **< 4 hours** | Technical Support Queue |
| **Account** | Normal | **< 4 hours** | Account Services |
| **Unrouted Review** | Needs Review | **< 30 minutes** | Support Triage Queue |

### Unrouted Escalation Fallback
- Requests with ambiguous categories or unassigned departments are flagged as `needs_review` and immediately dispatched to `TRIAGE_EMAIL` and `TRIAGE_SLACK_CHANNEL` with `unassigned_triage_alert`.
- If an unassigned urgent ticket remains in `needs_review` for over 30 minutes without assignment, automated operational alerts ping the duty manager.

---

## 3. Customer Communication & Notice Policy

1. **Guest Acknowledgement Controls**:
   - To prevent the public intake endpoint from being abused as an email spam relay (email bombing), unverified guest acknowledgements are subject to rate limiting (`RATE_LIMIT_PER_EMAIL_HOUR`).
   - Verified customers receive automated confirmation and secure access tokens to track ticket progress.
2. **Status Updates**:
   - Customers receive transactional notifications solely upon meaningful state changes (`assigned`, `in_progress`, `waiting_on_customer`, `resolved`, `closed`).
3. **Restricted Content**:
   - Outbound customer notices **never** request passwords, secret keys, or unredacted financial card information.

---

## 4. Privacy & Data Minimization Policy

1. **Automated PII & Credential Redaction**:
   - All notification payloads, Slack webhooks, and execution logs pass through automated sanitization (`app/security/sanitizer.py`).
   - Credit card numbers, Social Security Numbers, authorization bearer tokens, and password literals are redacted prior to persistence in notifications or log output.
2. **Access Control (Least Privilege)**:
   - Customers may only query and view tickets associated with their verified email address (`X-Customer-Token`).
   - Support agents can inspect ticket metadata and update statuses.
   - Only administrators may reassign departments or trigger retention purges.

---

## 5. Data Retention & Anonymization Policy

1. **Transactional Outbox Purge**:
   - Successfully delivered notifications (`status = 'sent'`) and exhausted notifications (`status = 'failed'`) older than **90 days** are automatically deleted from the PostgreSQL `notifications` table.
2. **Audit Event Pruning**:
   - System and agent audit event logs are retained for **90 days** for operational auditing, after which they are archived/purged.
3. **Customer PII Anonymization**:
   - Closed and resolved support requests older than **180 days** have their subject and message bodies replaced with `[RETENTION_ANONYMIZED]`.
   - Automated enforcement can be executed on a cron schedule using `POST /admin/retention/cleanup` or `python -m app.security.retention`.
