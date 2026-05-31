# Aurora LTS — SOC 2 Type I / ISO 27001 Readiness Assessment

**Date:** 2026-05-28  
**Prepared by:** Aurora Engineering  
**Scope:** ASG-Middleware (FastAPI backend), AuroraMacShell, accountant-portal, aurora-website  
**Target Frameworks:** SOC 2 Type I (Trust Service Criteria) · ISO 27001:2022  
**Current Posture:** Pre-audit — gap analysis and remediation roadmap

---

## Executive Summary

Aurora LTS has made substantial security investments as part of the P0/P1/P2 hardening programme. The platform is architecturally sound for a financial SaaS of this scale, with cryptographic device binding, multi-layer authentication, row-level data isolation, and immutable audit trails already in place. However, a formal SOC 2 or ISO 27001 certification requires formalising processes, completing documentation, and closing several operational gaps identified below.

**Estimated time to SOC 2 Type I readiness:** 10–14 weeks (with dedicated effort).  
**Estimated time to ISO 27001 certification:** 6–9 months (full ISMS establishment required).

---

## Section 1 — SOC 2 Type I Gap Analysis

SOC 2 assesses five Trust Service Criteria (TSC). Aurora's current posture against each:

### 1.1 Security (CC6, CC7, CC8, CC9) — Most Critical

| Control | Status | Evidence Location | Gap / Action |
|---------|--------|-------------------|-------------|
| Encryption at rest (AES-256) | ✅ | GCS CMEK, `services/gcp/storage.py`, `config/secrets.py` `aurora-pii-encryption-key` | Document key rotation schedule |
| Encryption in transit (TLS 1.3) | ✅ | Cloud Run HTTPS, `TLSPinning.swift` (macOS client), Tauri CSP `connect-src` | Verify TLS 1.3 enforcement in Cloud Load Balancer; disable TLS 1.1/1.2 |
| Access controls (RBAC) | ✅ | `middleware/auth_middleware.py`, `require_admin`, `require_accountant`, `require_org_access()` | Document role matrix in access control policy |
| MFA / strong authentication | ✅ | Secure Enclave + Touch ID (`DeviceIdentity.swift` v2), OTP (`accountant_auth.py`) | Enforce MFA for all admin paths in production (not just optional) |
| Privileged access management | ✅ | Break-glass tokens (`admin_break_glass.py`), IAP enforcement | Create privileged access request/approval procedure |
| Secrets management | ✅ | Secret Manager (13 secrets), `config/secrets.py` `validate_all_secrets()` | Document rotation schedule; enforce rotation in SM versioning policy |
| Vulnerability management | ⚠️ | Manual | Implement Dependabot / `pip-audit` in CI; schedule quarterly pen test |
| Incident response plan | ❌ | Absent | Write and test IRP (see Section 3) |
| Security training | ❌ | Absent | Conduct annual security awareness training; document completion |
| Penetration testing | ❌ | Absent | Commission third-party pen test ≥ annually |
| Change management | ⚠️ | `cloudbuild.yaml` (blue-green), git history | Formalise change review process; add PR approval requirement |
| Network segmentation | ✅ | Cloud SQL private IP, VPC egress | Document network topology |
| Logging & monitoring | ✅ | `python-json-logger`, Cloud Logging, `X-Request-ID` middleware | Configure log-based alerts for 5xx rate, latency, auth failures |
| Intrusion detection | ⚠️ | Anomaly detection (P2-20 heuristics) | Add Cloud Armor WAF; configure Security Command Center |

### 1.2 Availability (A1)

| Control | Status | Gap |
|---------|--------|-----|
| Uptime SLA | ⚠️ | Cloud Run SLA = 99.95%; no formal customer-facing SLA documented |
| Multi-zone redundancy | ✅ (partial) | Cloud Run multi-zone; Cloud SQL REGIONAL failover now enabled |
| Backup & recovery | ✅ | Cloud SQL PITR 7 days; GCS versioning on vault bucket | RTO/RPO not formally documented |
| Disaster recovery plan | ❌ | Write DR runbook; test annually |
| Capacity planning | ⚠️ | Cloud Run auto-scales to max=10; DB pool budget monitored | Document capacity forecast |

### 1.3 Processing Integrity (PI1)

| Control | Status | Gap |
|---------|--------|-----|
| Completeness / accuracy of processing | ✅ | ITA allocation audit log (`ita_audit_log`), immutability guards (`compliance/immutability.py`), hash-chain on compliance exports | Document data validation procedures |
| Error handling | ✅ | Global exception handler (P1-06), structured error responses | |
| Processing monitoring | ⚠️ | ActionLog covers key operations | Add BigQuery anomaly queries on processing failures |

### 1.4 Confidentiality (C1)

| Control | Status | Gap |
|---------|--------|-----|
| Data classification | ❌ | No formal data classification policy | Write policy: PII / financial / internal / public |
| Access on need-to-know | ✅ | PostgreSQL RLS (P1-04), org-scoped queries | Document data access matrix |
| NDA with employees/contractors | ❌ | Unknown — organisational | Ensure all personnel have signed NDAs |
| Third-party data processing agreements | ❌ | Unknown | Execute DPAs with GCP, SendGrid, PayPlus, Inforu, Make.com |

### 1.5 Privacy (P1–P8)

| Control | Status | Gap |
|---------|--------|-----|
| Privacy notice | ✅ | `/privacy` page on aurora-website | Ensure notice covers all data collected (phone, tax ID, documents) |
| Consent collection | ⚠️ | T&C acceptance in onboarding | Add explicit consent checkboxes for each data purpose |
| Data subject requests (DSAR) | ✅ | `compliance/dsar.py`, `admin_compliance.py` | Test full DSAR flow; document response SLA (30 days per GDPR) |
| Data retention & deletion | ✅ | 7-year GCS retention policy; `compliance/immutability.py` | Document retention schedule per data category |
| Data minimisation | ⚠️ | PII stored in DB; column-level encryption (P1-23) | Audit which fields are truly necessary |
| Cross-border data transfers | ✅ | All data in me-west1 (IL) | Document for EU clients if applicable |

---

## Section 2 — ISO 27001:2022 Clause Gap Analysis

ISO 27001 requires an **Information Security Management System (ISMS)**. The technical controls are largely in place; the documentation and process layer is the primary gap.

### Clause 4 — Context of the Organisation
- ✅ Stakeholders identified (businesses, accountants, tax authority)
- ❌ **ISMS scope document** not written
- ❌ **Information security policy** not written

### Clause 5 — Leadership
- ❌ **Management commitment** statement needed
- ❌ **Information security roles** (CISO / security owner) not formally assigned

### Clause 6 — Planning
- ❌ **Risk assessment methodology** not documented
- ❌ **Risk register** not created (known risks: see P0 audit findings)
- ❌ **Statement of Applicability (SoA)** not prepared

### Clause 7 — Support
- ❌ **Security awareness training programme** not established
- ❌ **Document control procedure** not written
- ✅ Competency (engineering team has security skills — evidenced by P0–P2 work)

### Clause 8 — Operation
- ✅ Operational security controls largely implemented (see SOC 2 Section 1)
- ❌ **Supplier security assessment** procedure not documented
- ❌ **Change management procedure** not formalised
- ❌ **Incident management procedure** not written

### Clause 9 — Performance Evaluation
- ⚠️ Monitoring exists (Cloud Logging, Sentry) but no formal **internal audit schedule**
- ❌ **Management review** procedure not established
- ❌ **KPIs for ISMS effectiveness** not defined

### Clause 10 — Improvement
- ❌ **Corrective action procedure** not documented
- ❌ **Continual improvement** evidence not gathered

### Annex A Controls (ISO 27001:2022 — selected highlights)

| Control # | Control | Status |
|-----------|---------|--------|
| A.5.1 | Information security policies | ❌ Not written |
| A.5.9 | Inventory of information assets | ❌ Not documented |
| A.5.15 | Access control | ✅ Implemented (RBAC + RLS) |
| A.5.17 | Authentication information | ✅ Secure Enclave, OTP, bcrypt |
| A.5.23 | Information security for cloud services | ⚠️ GCP-native controls used; DPA not executed |
| A.6.3 | Information security awareness | ❌ No programme |
| A.7.8 | Clear desk/screen policy | ❌ Not established |
| A.8.7 | Protection against malware | ⚠️ GCS DLP scanning, Sentry; no endpoint EDR |
| A.8.8 | Management of technical vulnerabilities | ❌ No formal patch management |
| A.8.12 | Data leakage prevention | ✅ GCP DLP (P1 stub → production when configured) |
| A.8.16 | Monitoring activities | ✅ Structured logging, anomaly detection (P2-20) |
| A.8.24 | Use of cryptography | ✅ AES-256 at rest, TLS 1.3 in transit, ECDSA device binding |
| A.8.28 | Secure coding | ⚠️ Evidenced by P0 hardening; no formal secure SDLC policy |
| A.8.29 | Security testing in development | ❌ No automated SAST/DAST pipeline |

---

## Section 3 — Priority Remediation Roadmap

### Phase 1 — Foundation Documents (Weeks 1–4)

These documents unlock everything else. They can be created by the engineering/leadership team without external consultants.

1. **Information Security Policy** (1 page) — management commitment, scope, objectives
2. **Data Classification Policy** — PII / financial / internal / public definitions
3. **Access Control Policy** — role matrix, principle of least privilege, review cadence
4. **Incident Response Plan** — detection, triage, containment, notification, post-mortem
5. **Risk Register** — list known risks (from P0 audit), likelihood, impact, treatment
6. **Asset Inventory** — systems, data stores, integrations, owners

### Phase 2 — Process Controls (Weeks 5–10)

1. Implement **Dependabot** (GitHub) + `pip-audit` in `cloudbuild.yaml` for dependency CVEs
2. Configure **Security Command Center** (GCP) for infrastructure threat detection
3. Set up **Cloud Armor WAF** in front of Cloud Run (rate limiting + OWASP rules)
4. Add **log-based alerts** in Cloud Logging:
   - Error rate > 1% over 5 minutes → PagerDuty
   - Auth failure rate > 50/min → Slack + email
   - Anomaly events with severity=critical → immediate alert
5. Execute **DPAs** with all data processors (GCP, SendGrid, PayPlus, Inforu, Make.com)
6. Conduct **security awareness training** (1 hour, documented completion)
7. Commission **penetration test** (third-party, black-box)

### Phase 3 — SOC 2 Type I Audit Readiness (Weeks 11–14)

1. Engage SOC 2 auditor (e.g., Schellman, Coalfire, A-LIGN)
2. Define **observation period** (SOC 2 Type I = point-in-time; Type II = 6 months)
3. Compile **evidence portfolio**:
   - Git history of security controls
   - Cloud Logging exports
   - Penetration test report
   - Risk register and treatment documentation
   - Training completion records
4. Conduct **internal audit** against SOC 2 TSC
5. Remediate auditor findings
6. Issue **SOC 2 Type I report**

### Phase 4 — ISO 27001 Certification (Months 3–9)

1. Engage ISO 27001 certification body (UKAS/ANAB accredited)
2. Prepare **Statement of Applicability** (Annex A control mapping)
3. Establish **ISMS governance** (security committee, review cadence)
4. Complete **Stage 1 audit** (document review)
5. Complete **Stage 2 audit** (on-site/remote control testing)
6. Issue **ISO 27001 certificate** (valid 3 years with annual surveillance audits)

---

## Section 4 — Current Security Posture Scorecard

| Domain | Score | Notes |
|--------|-------|-------|
| Authentication & Access Control | 9/10 | Best-in-class: SE + biometric + OIDC + break-glass + RLS |
| Encryption | 8/10 | At-rest (CMEK + column-level) + in-transit (TLS + pinning) |
| Audit & Logging | 7/10 | ActionLog + Cloud Logging + anomaly detection; BigQuery stub |
| Secrets Management | 8/10 | Secret Manager for all 13 secrets; rotation policy TBD |
| Vulnerability Management | 3/10 | No automated scanning, no pen test history |
| Incident Response | 1/10 | No documented IRP |
| Compliance Documentation | 2/10 | Privacy/terms pages exist; ISMS absent |
| Supplier Risk | 3/10 | DPAs not executed with any vendor |
| Change Management | 6/10 | Blue-green CI/CD in place; approval process informal |
| Business Continuity | 5/10 | Cloud SQL PITR + multi-zone; no tested DR runbook |
| **Overall** | **5.2/10** | Strong technical controls; process/documentation layer is the gap |

---

## Section 5 — Key Evidence Already Available for Auditors

The following artefacts demonstrate control implementation and can be provided to auditors directly:

| Artefact | File / Location | Control Covered |
|----------|-----------------|-----------------|
| Multi-layer auth middleware | `server_files/app/middleware/auth_middleware.py` | CC6.1, A.5.15 |
| Secure Enclave device binding | `AuroraMacShell/DeviceIdentity.swift` | CC6.1, A.8.24 |
| PostgreSQL RLS | `server_files/app/migrations/migrate_phase*.py` | CC6.6, A.5.15 |
| Column-level PII encryption | `server_files/app/services/gcp/secrets.py` + models | CC6.7, A.8.24 |
| Secret Manager integration | `infra/terraform/secrets.tf` | CC6.1, A.5.17 |
| Immutable audit trail | `server_files/app/services/compliance/immutability.py` | CC7.2, A.8.16 |
| DSAR implementation | `server_files/app/services/compliance/dsar.py` | P8.1, A.5.34 |
| Structured logging | `server_files/app/main.py` (`X-Request-ID` middleware) | CC7.2, A.8.16 |
| Blue-green deployment | `cloudbuild.yaml` | CC8.1, A.8.32 |
| AML sanctions screening | `server_files/app/services/compliance/sanctions.py` | A.5.19 |
| Anomaly detection | `server_files/app/services/compliance/anomaly_detection.py` | CC7.2, A.8.16 |
| GCS 7-year retention | `infra/terraform/main.tf` (vault bucket) | C1.2, A.5.33 |
| Privacy policy | `aurora-website/app/privacy/page.tsx` | P1.1, A.5.34 |
| Terms of service | `aurora-website/app/terms/page.tsx` | P1.1 |
