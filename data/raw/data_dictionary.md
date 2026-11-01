# Data dictionary: Enterprise ITSM synthetic datasets

All records are **synthetic**, generated locally with `mistralai/Mistral-7B-Instruct-v0.3` on a Kaggle GPU (inference only) for the fictional organization "Orbitel Systems". Structured fields, IDs, links and outcomes were assigned deterministically in Python; text fields were written by the model and validated by deterministic rules.

Datasets: `historical_incidents.csv` (1,500 rows), `incident_sops.csv` (60), `incident_policies.csv` (60).
Links: tickets.sop_id -> sops.sop_id; tickets.policy_id -> policies.policy_id; policies.sop_id -> sops.sop_id (1:1).

## historical_incidents.csv

| # | Column | Description | Allowed values |
|---|---|---|---|
| 1 | `incident_id` | Unique ticket ID assigned by Python on acceptance (INC####### for incidents, RITM####### for requests; one shared counter). |  |
| 2 | `ticket_type` | INCIDENT, SECURITY_INCIDENT, ACCESS_REQUEST or SERVICE_REQUEST. | INCIDENT, SECURITY_INCIDENT, ACCESS_REQUEST, SERVICE_REQUEST |
| 3 | `title` | Short ticket title as written by the reporter or monitoring system. |  |
| 4 | `description` | Reporter's description of the problem or request (no root cause stated). |  |
| 5 | `category` | Top-level operational domain. | NETWORK, VPN, ENDPOINT, SOFTWARE, WEBSITE_ACCESS, AI_TOOLS, IDENTITY_ACCESS, APPLICATION, INFRASTRUCTURE, DATABASE, SECURITY |
| 6 | `subcategory` | Specific problem/request type within the category (matches the linked SOP and policy). |  |
| 7 | `service` | Business/IT service affected or requested. |  |
| 8 | `affected_component` | Concrete synthetic component (host, device, app, gateway, group) involved. |  |
| 9 | `user_role` | Role of the requester / affected user (or on-call SRE for monitoring alerts). | EMPLOYEE, CONTRACTOR, DEVELOPER, DATA_SCIENTIST, MANAGER, FINANCE_ANALYST, HR_SPECIALIST, SALES_REPRESENTATIVE, IT_SUPPORT_ANALYST, SYSTEM_ADMINISTRATOR, DATABASE_ADMINISTRATOR, NETWORK_ENGINEER, SECURITY_ANALYST, SITE_RELIABILITY_ENGINEER |
| 10 | `department` | Requester's department. | ENGINEERING, IT_OPERATIONS, SECURITY, FINANCE, HR, SALES, MARKETING, DATA_ANALYTICS, CUSTOMER_SUPPORT, LEGAL, OPERATIONS |
| 11 | `environment` | PRODUCTION, STAGING, DEVELOPMENT or CORPORATE_IT (end-user devices and office IT). | PRODUCTION, STAGING, DEVELOPMENT, CORPORATE_IT |
| 12 | `impact` | Low / Medium / High. | Low, Medium, High |
| 13 | `urgency` | Low / Medium / High. | Low, Medium, High |
| 14 | `priority` | Derived deterministically from the impact x urgency matrix: Low / Medium / High / Critical. | Low, Medium, High, Critical |
| 15 | `ticket_status` | Lifecycle state: CLOSED, PENDING_APPROVAL or ESCALATED. | CLOSED, PENDING_APPROVAL, ESCALATED |
| 16 | `requested_resource` | Requests only: the resource being requested (null for incidents). |  |
| 17 | `requested_access_type` | Requests only: USER, READ_ONLY, READ_WRITE, PRIVILEGED, INSTALL, NETWORK or EXCEPTION. | USER, READ_ONLY, READ_WRITE, PRIVILEGED, INSTALL, NETWORK, EXCEPTION |
| 18 | `requested_action` | Requests only: GRANT, REVOKE, INSTALL, ALLOWLIST, ENROLL, MODIFY or EXCEPTION. | GRANT, REVOKE, INSTALL, ALLOWLIST, ENROLL, MODIFY, EXCEPTION |
| 19 | `requested_application` | Software/application requested or involved (null when not applicable). |  |
| 20 | `website_or_domain` | Website/domain requested or affected (null when not applicable). |  |
| 21 | `ai_tool_name` | Organizational or external AI tool requested or affected (null when not applicable). |  |
| 22 | `business_justification` | Requests only: requester's justification (null for incidents). |  |
| 23 | `requested_duration` | Requests only: PERMANENT, ONE_TIME or a time-boxed duration. | PERMANENT, ONE_TIME, 4_HOURS, 8_HOURS, 24_HOURS, 7_DAYS, 30_DAYS, 90_DAYS |
| 24 | `access_scope` | Requests only: scope of the requested access. | USER_DEVICE, APPLICATION_ROLE, DIRECTORY_GROUP, NETWORK_SEGMENT, REMOTE_ACCESS, PRODUCTION_SYSTEM, WEB_DOMAIN, AI_PLATFORM, SECURITY_CONTROL |
| 25 | `created_at` | Ticket creation time (YYYY-MM-DD HH:MM:SS, synthetic, 2025-01 to 2026-06). |  |
| 26 | `acknowledged_at` | Time the ticket was acknowledged (>= created_at). |  |
| 27 | `resolved_at` | Closure time for RESOLVED / RESOLVED_WITH_WORKAROUND / REJECTED tickets; null while PENDING or ESCALATED. |  |
| 28 | `resolution_time_minutes` | Whole minutes from created_at to resolved_at; null when resolved_at is null. |  |
| 29 | `symptoms` | Observable symptoms reported or detected. |  |
| 30 | `observed_state` | State of the system/request at triage time. |  |
| 31 | `telemetry_type` | Primary evidence source type embedded in diagnostic_data. | LOGS, METRICS, TRACES, HEALTH_CHECK, NETWORK_OBSERVATION, APPLICATION_ERROR, VPN_EVENT, AUTH_EVENT, ENDPOINT_EVENT, SECURITY_ALERT, REQUEST_RECORD |
| 32 | `diagnostic_data` | Synthetic telemetry excerpt: log lines, metrics, health checks, VPN/auth events, or entitlement lookups. |  |
| 33 | `initial_hypothesis` | First working hypothesis (may be wrong; see scenario_type). |  |
| 34 | `initial_evidence` | Evidence that motivated the initial hypothesis (observations, not conclusions). |  |
| 35 | `investigation_steps` | Ordered investigation steps performed. |  |
| 36 | `reinvestigation_trigger` | Why investigation was reopened (null when no re-investigation occurred). |  |
| 37 | `new_evidence` | Evidence gathered during re-investigation (null when none). |  |
| 38 | `root_cause_category` | Final root-cause class; UNDETERMINED when evidence was insufficient. | CONFIGURATION_ERROR, EXPIRED_CREDENTIAL, CERTIFICATE_EXPIRY, RESOURCE_EXHAUSTION, CAPACITY_LIMIT, DEPENDENCY_FAILURE, NETWORK_FAILURE, DNS_MISCONFIGURATION, FIREWALL_RULE, SOFTWARE_DEFECT, VERSION_CONFLICT, DEPLOYMENT_CHANGE, HARDWARE_FAULT, MISSING_ENTITLEMENT, POLICY_RESTRICTION, SECURITY_EVENT, USER_ERROR, UNDETERMINED |
| 39 | `root_cause` | Final accepted root cause; starts with 'Provisional:' when the RCA is not confirmed. |  |
| 40 | `contributing_factors` | Secondary factors that enabled or worsened the issue. |  |
| 41 | `updated_root_cause` | Revised RCA recorded during re-investigation (null when the RCA was not revised). |  |
| 42 | `updated_resolution_plan` | Revised plan after re-investigation or rollback (null otherwise). |  |
| 43 | `sop_id` | FK -> incident_sops.sop_id. |  |
| 44 | `policy_id` | FK -> incident_policies.policy_id (the policy governing this ticket's action_type). |  |
| 45 | `resolution_plan` | Initial resolution plan derived from the SOP. |  |
| 46 | `initial_action` | First action executed, or the proposed action when it was not executed. |  |
| 47 | `initial_action_result` | Outcome of the first action (or why it was not executed). |  |
| 48 | `initial_verification_result` | PASSED / FAILED / NOT_RUN for the check after the first action. | PASSED, FAILED, NOT_RUN |
| 49 | `actions_taken` | Chronological summary of all actions actually performed. |  |
| 50 | `action_type` | Governed remediation/fulfilment action (equals the linked SOP's action_type). | UPDATE_CONFIGURATION, UPDATE_DNS_RECORD, UPDATE_FIREWALL_RULE, RESTART_NETWORK_DEVICE, GRANT_NETWORK_ACCESS, PROVISION_VPN_PROFILE, RESET_CREDENTIALS, RESET_MFA, SCALE_RESOURCES, REMEDIATE_ENDPOINT, REPAIR_INSTALLATION, ALLOWLIST_APPLICATION, INSTALL_SOFTWARE, UPDATE_SOFTWARE, ALLOWLIST_DOMAIN, RESTART_SERVICE, ADD_GROUP_MEMBERSHIP, GRANT_ACCESS, GRANT_PRIVILEGED_ACCESS, REVOKE_ACCESS, UNLOCK_ACCOUNT, RENEW_CERTIFICATE, CLEAR_CACHE, ROLLBACK_DEPLOYMENT, FREE_DISK_SPACE, RERUN_JOB, TERMINATE_DB_SESSIONS, TUNE_DATABASE, APPLY_DATABASE_CHANGE, QUARANTINE_EMAIL, ISOLATE_ENDPOINT, DISABLE_SECURITY_CONTROL |
| 51 | `authorization_decision` | ALLOW, ALLOW_WITH_APPROVAL, DENY or ESCALATE_FOR_REVIEW. | ALLOW, ALLOW_WITH_APPROVAL, DENY, ESCALATE_FOR_REVIEW |
| 52 | `authorization_reason` | Policy-based reason for the authorization decision. |  |
| 53 | `approval_required` | Boolean: whether the linked policy requires approval for this action. | True / False |
| 54 | `approval_status` | NOT_REQUIRED, PENDING, APPROVED, REJECTED or NOT_APPLICABLE (no approval path: DENY/escalate). | NOT_REQUIRED, PENDING, APPROVED, REJECTED, NOT_APPLICABLE |
| 55 | `approver_role` | Approver role from the linked policy (null when approval is not required). | LINE_MANAGER, APPLICATION_OWNER, DATA_OWNER, SECURITY_TEAM, IT_SERVICE_OWNER, CHANGE_ADVISORY_BOARD, AI_GOVERNANCE_BOARD |
| 56 | `executor_role` | Role that executes (or would execute) the action; equals the policy's required_executor_role. | AUTOMATION_AGENT, SERVICE_DESK_L1, SERVICE_DESK_L2, ENDPOINT_ENGINEER, NETWORK_ENGINEER, IAM_ADMINISTRATOR, SYSTEM_ADMINISTRATOR, DATABASE_ADMINISTRATOR, SRE_ENGINEER, SECURITY_ANALYST |
| 57 | `execution_status` | Final execution state: SUCCEEDED, FAILED, NOT_EXECUTED or ROLLED_BACK. | SUCCEEDED, FAILED, NOT_EXECUTED, ROLLED_BACK |
| 58 | `execution_result` | Description of the final execution outcome. |  |
| 59 | `verification_method` | How the outcome was verified (null when nothing was executed). |  |
| 60 | `final_action` | Second/last action after re-investigation or rollback (null when the first action was final). |  |
| 61 | `final_action_result` | Outcome of final_action (null when final_action is null). |  |
| 62 | `final_verification_result` | PASSED / FAILED / NOT_RUN for the final check. RESOLVED requires PASSED. | PASSED, FAILED, NOT_RUN |
| 63 | `resolution_status` | RESOLVED, RESOLVED_WITH_WORKAROUND, REJECTED, PENDING or ESCALATED. | RESOLVED, RESOLVED_WITH_WORKAROUND, REJECTED, PENDING, ESCALATED |
| 64 | `escalation_required` | Boolean: True when the ticket was escalated to a human team. | True / False |
| 65 | `escalation_reason` | Why it was escalated (required when escalation_required is True). |  |
| 66 | `resolution_summary` | Closure/communication summary for the requester. |  |
| 67 | `repair_iterations` | Number of remediation actions executed (0 when nothing was executed). |  |
| 68 | `scenario_type` | Adaptive-remediation scenario label assigned by Python (for evaluation and stratified splits). | success_first_attempt, success_after_approval, denied_by_policy, pending_approval, approval_rejected, failed_then_reinvestigated, incorrect_hypothesis_corrected, exec_success_verification_failed, failed_action_escalated, escalated_no_permitted_action, insufficient_evidence_provisional_rca, rollback_after_adverse_effect |

## incident_sops.csv

| # | Column | Description | Allowed values |
|---|---|---|---|
| 1 | `sop_id` | Unique SOP ID (SOP-0001...). |  |
| 2 | `sop_title` | Procedure title. |  |
| 3 | `ticket_type` | Ticket type this SOP handles. | INCIDENT, SECURITY_INCIDENT, ACCESS_REQUEST, SERVICE_REQUEST |
| 4 | `category` | Category. | NETWORK, VPN, ENDPOINT, SOFTWARE, WEBSITE_ACCESS, AI_TOOLS, IDENTITY_ACCESS, APPLICATION, INFRASTRUCTURE, DATABASE, SECURITY |
| 5 | `subcategory` | Subcategory. |  |
| 6 | `service` | Service. |  |
| 7 | `trigger_condition` | When to apply this SOP. |  |
| 8 | `symptoms` | Typical symptoms. |  |
| 9 | `prerequisites` | Access, tools, and information required before starting. |  |
| 10 | `diagnostic_steps` | Numbered, specific diagnostic steps (commands, consoles, checks). |  |
| 11 | `decision_condition` | Observable conditions that select the remediation branch. |  |
| 12 | `recommended_action` | Permitted remediation/fulfilment action. |  |
| 13 | `action_type` | Governed action type (enum), identical to the linked policy's governed action. | UPDATE_CONFIGURATION, UPDATE_DNS_RECORD, UPDATE_FIREWALL_RULE, RESTART_NETWORK_DEVICE, GRANT_NETWORK_ACCESS, PROVISION_VPN_PROFILE, RESET_CREDENTIALS, RESET_MFA, SCALE_RESOURCES, REMEDIATE_ENDPOINT, REPAIR_INSTALLATION, ALLOWLIST_APPLICATION, INSTALL_SOFTWARE, UPDATE_SOFTWARE, ALLOWLIST_DOMAIN, RESTART_SERVICE, ADD_GROUP_MEMBERSHIP, GRANT_ACCESS, GRANT_PRIVILEGED_ACCESS, REVOKE_ACCESS, UNLOCK_ACCOUNT, RENEW_CERTIFICATE, CLEAR_CACHE, ROLLBACK_DEPLOYMENT, FREE_DISK_SPACE, RERUN_JOB, TERMINATE_DB_SESSIONS, TUNE_DATABASE, APPLY_DATABASE_CHANGE, QUARANTINE_EMAIL, ISOLATE_ENDPOINT, DISABLE_SECURITY_CONTROL |
| 14 | `approval_required` | Boolean, consistent with the linked policy. | True / False |
| 15 | `required_role` | Executor role, consistent with the linked policy. | AUTOMATION_AGENT, SERVICE_DESK_L1, SERVICE_DESK_L2, ENDPOINT_ENGINEER, NETWORK_ENGINEER, IAM_ADMINISTRATOR, SYSTEM_ADMINISTRATOR, DATABASE_ADMINISTRATOR, SRE_ENGINEER, SECURITY_ANALYST |
| 16 | `risk_level` | LOW / MEDIUM / HIGH / CRITICAL. | LOW, MEDIUM, HIGH, CRITICAL |
| 17 | `rollback_action` | How to safely undo the action ('Not applicable: ...' only when no change is made). |  |
| 18 | `verification_steps` | Numbered checks that confirm success. |  |
| 19 | `failure_condition` | What counts as failure of the action or verification. |  |
| 20 | `escalation_condition` | When and to whom to escalate. |  |
| 21 | `sop_version` | Version string. |  |
| 22 | `status` | ACTIVE. | ACTIVE |

## incident_policies.csv

| # | Column | Description | Allowed values |
|---|---|---|---|
| 1 | `policy_id` | Unique policy ID (POL-0001...). |  |
| 2 | `sop_id` | FK -> incident_sops.sop_id (1:1). |  |
| 3 | `policy_name` | Policy name. |  |
| 4 | `ticket_type` | Ticket type governed. | INCIDENT, SECURITY_INCIDENT, ACCESS_REQUEST, SERVICE_REQUEST |
| 5 | `category` | Category. | NETWORK, VPN, ENDPOINT, SOFTWARE, WEBSITE_ACCESS, AI_TOOLS, IDENTITY_ACCESS, APPLICATION, INFRASTRUCTURE, DATABASE, SECURITY |
| 6 | `subcategory` | Subcategory. |  |
| 7 | `service` | Service. |  |
| 8 | `condition` | Condition under which the policy applies. |  |
| 9 | `user_role` | Eligible requester roles, pipe-separated ('|'); 'NONE' if no role is eligible. |  |
| 10 | `allowed_action` | Actions permitted under this policy. |  |
| 11 | `restricted_action` | Actions permitted only with approval or extra controls. |  |
| 12 | `prohibited_action` | Actions that are never permitted. |  |
| 13 | `default_decision` | Machine-readable decision for the governed action: ALLOW, ALLOW_WITH_APPROVAL or DENY. | ALLOW, ALLOW_WITH_APPROVAL, DENY |
| 14 | `required_executor_role` | Role that must execute the action. | AUTOMATION_AGENT, SERVICE_DESK_L1, SERVICE_DESK_L2, ENDPOINT_ENGINEER, NETWORK_ENGINEER, IAM_ADMINISTRATOR, SYSTEM_ADMINISTRATOR, DATABASE_ADMINISTRATOR, SRE_ENGINEER, SECURITY_ANALYST |
| 15 | `risk_level` | LOW / MEDIUM / HIGH / CRITICAL. | LOW, MEDIUM, HIGH, CRITICAL |
| 16 | `approval_required` | Boolean. | True / False |
| 17 | `approver_role` | Authorized approver (null when no approval is required). | LINE_MANAGER, APPLICATION_OWNER, DATA_OWNER, SECURITY_TEAM, IT_SERVICE_OWNER, CHANGE_ADVISORY_BOARD, AI_GOVERNANCE_BOARD |
| 18 | `business_impact_limit` | Limits on business impact (change windows, blast radius, maximum duration). |  |
| 19 | `security_constraint` | Security controls that must hold. |  |
| 20 | `rollback_required` | Boolean: a rollback plan must exist before execution. | True / False |
| 21 | `audit_required` | Boolean: action must be audit-logged. | True / False |
| 22 | `exception_condition` | How exceptions are requested/granted (never bypasses approval). |  |
| 23 | `policy_version` | Version string. |  |
| 24 | `status` | ACTIVE. | ACTIVE |

## Scenario semantics (historical_incidents.scenario_type)

| Scenario | Authorization | Approval | Execution | Initial verif. | Final verif. | Resolution | Ticket status | Escalated | Iterations |
|---|---|---|---|---|---|---|---|---|---|
| success_first_attempt | policy default | APPROVED (NOT_REQUIRED under ALLOW policies) | SUCCEEDED | PASSED | PASSED | RESOLVED | CLOSED | False | 1 |
| success_after_approval | policy default | APPROVED (NOT_REQUIRED under ALLOW policies) | SUCCEEDED | PASSED | PASSED | RESOLVED | CLOSED | False | 1 |
| denied_by_policy | DENY | NOT_APPLICABLE (NOT_REQUIRED under ALLOW policies) | NOT_EXECUTED | NOT_RUN | NOT_RUN | REJECTED | CLOSED | False | 0 |
| pending_approval | policy default | PENDING (NOT_REQUIRED under ALLOW policies) | NOT_EXECUTED | NOT_RUN | NOT_RUN | PENDING | PENDING_APPROVAL | False | 0 |
| approval_rejected | policy default | REJECTED (NOT_REQUIRED under ALLOW policies) | NOT_EXECUTED | NOT_RUN | NOT_RUN | REJECTED | CLOSED | False | 0 |
| failed_then_reinvestigated | policy default | APPROVED (NOT_REQUIRED under ALLOW policies) | SUCCEEDED | FAILED | PASSED | RESOLVED | CLOSED | False | 2 |
| incorrect_hypothesis_corrected | policy default | APPROVED (NOT_REQUIRED under ALLOW policies) | SUCCEEDED | FAILED | PASSED | RESOLVED | CLOSED | False | 2 |
| exec_success_verification_failed | policy default | APPROVED (NOT_REQUIRED under ALLOW policies) | SUCCEEDED | FAILED | FAILED | ESCALATED | ESCALATED | True | 1 |
| failed_action_escalated | policy default | APPROVED (NOT_REQUIRED under ALLOW policies) | FAILED | FAILED | NOT_RUN | ESCALATED | ESCALATED | True | 1 |
| escalated_no_permitted_action | ESCALATE_FOR_REVIEW | NOT_APPLICABLE (NOT_REQUIRED under ALLOW policies) | NOT_EXECUTED | NOT_RUN | NOT_RUN | ESCALATED | ESCALATED | True | 0 |
| insufficient_evidence_provisional_rca | policy default | APPROVED (NOT_REQUIRED under ALLOW policies) | SUCCEEDED | PASSED | PASSED | RESOLVED_WITH_WORKAROUND | CLOSED | False | 1 |
| rollback_after_adverse_effect | policy default | APPROVED (NOT_REQUIRED under ALLOW policies) | ROLLED_BACK | FAILED | PASSED | ESCALATED | ESCALATED | True | 2 |

## Schema changes relative to the original specification

- Removed `verification_result` (duplicate of `final_verification_result`).
- Removed `is_synthetic` (constant True; provenance is stated in this dictionary).
- Added `incident_policies.default_decision` (machine-readable ALLOW / ALLOW_WITH_APPROVAL / DENY).