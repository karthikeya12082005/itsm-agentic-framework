# Validation report: final

**Status: PASS** | generated 2026-10-31T09:02:08 | model `mistralai/Mistral-7B-Instruct-v0.3`

## Record counts

| Dataset | Requested | Accepted |
|---|---|---|
| sops | 60 | 60 |
| policies | 60 | 60 |
| tickets | 1500 | 1500 |

## Generation counters (all attempts, including rejected and regenerated records)

| Counter | SOPs | Policies | Tickets |
|---|---|---|---|
| attempts | 71 | 87 | 1577 |
| accepted | 60 | 60 | 1500 |
| rejected | 11 | 27 | 77 |
| parse_failures | 2 | 1 | 37 |
| schema_failures | 0 | 13 | 14 |
| enum_failures | 0 | 0 | 0 |
| duplicate_rejections | 0 | 0 | 0 |
| fk_failures | 0 | 0 | 0 |
| policy_rule_violations | 3 | 7 | 0 |
| verification_consistency_errors | 0 | 0 | 1 |
| time_errors | 0 | 0 | 0 |
| semantic_failures | 6 | 6 | 25 |
| prompt_too_long | 0 | 0 | 0 |
| repairs_applied | 0 | 32 | 414 |
| regenerations | 11 | 27 | 77 |
| skeleton_refreshes | 0 | 0 | 0 |

## Integrity checks

| Check | Result |
|---|---|
| count_sops_exact | PASS |
| count_policies_exact | PASS |
| count_tickets_exact | PASS |
| sop_schema_exact | PASS |
| policy_schema_exact | PASS |
| ticket_schema_exact | PASS |
| sop_ids_unique | PASS |
| policy_ids_unique | PASS |
| ticket_ids_unique | PASS |
| ticket_id_format | PASS |
| sop_policy_enums_and_required_fields | PASS |
| sop_policy_compatible | PASS |
| policy_sop_fk_valid | PASS |
| ticket_rows_all_valid | PASS |
| ticket_fk_valid | PASS |
| policy_rules_respected | PASS |
| verification_consistency | PASS |
| time_consistency | PASS |
| no_exact_duplicates | PASS |
| no_near_duplicates | PASS |
| incident_share_70_80 | PASS |
| all_scenario_types_present | PASS |
| all_categories_present | PASS |
| every_sop_used_by_tickets | PASS |
| all_required_use_cases_covered | PASS |
| priority_variety | PASS |

- Final row failures: 0
- Exact duplicates: 0
- Near duplicates (Jaccard >= 0.6): 0
- Incident share: 75.0%
- Policy conflicts resolved (most restrictive wins): 0

## Top generation-time rejection reasons

- **sops**: quality (14), policy (3), parse (2)
- **policies**: schema (14), policy (7), quality (6), parse (1)
- **tickets**: parse (37), semantic (16), schema (14), quality (13), verify (1)

## Use-case coverage (ticket counts)

| Use case | Tickets |
|---|---|
| Chrome installation | 20 |
| VS Code installation | 20 |
| Approved developer tools (approval) | 21 |
| Prohibited software requests | 21 |
| Install / update failures & version conflicts | 75 |
| Application execution restrictions | 25 |
| VPN access requests | 24 |
| VPN auth / MFA / expired profile / config | 125 |
| DNS / firewall / proxy / routing / LAN | 125 |
| Restricted network segment requests | 24 |
| Website allowlisting requests | 21 |
| Website blocked by policy / prohibited sites | 49 |
| Corporate website availability | 25 |
| Approved organizational AI tools | 96 |
| Unapproved external AI tools | 23 |
| Lockout / password / MFA / SSO | 100 |
| Access provisioning, groups, removal | 67 |
| Privilege escalation requests | 23 |
| Requests exceeding entitlement (role-ineligible denials) | 28 |
| Application latency / availability / dependencies | 100 |
| Certificates & deployments | 50 |
| CPU / disk / service failures / backups | 100 |
| Monitoring alerts | 25 |
| Database connectivity / performance / changes | 96 |
| Security & compliance incidents | 150 |
| Security-sensitive exception requests | 20 |

## Distribution: ticket_type

| Value | Count |
|---|---|
| INCIDENT | 975 |
| ACCESS_REQUEST | 210 |
| SERVICE_REQUEST | 165 |
| SECURITY_INCIDENT | 150 |

## Distribution: category

| Value | Count |
|---|---|
| IDENTITY_ACCESS | 190 |
| SECURITY | 170 |
| SOFTWARE | 157 |
| APPLICATION | 150 |
| VPN | 149 |
| NETWORK | 149 |
| INFRASTRUCTURE | 125 |
| AI_TOOLS | 119 |
| ENDPOINT | 100 |
| DATABASE | 96 |
| WEBSITE_ACCESS | 95 |

## Distribution: subcategory

| Value | Count |
|---|---|
| PHISHING_REPORT | 38 |
| SUSPICIOUS_SIGN_IN | 38 |
| MALWARE_DETECTION | 37 |
| DLP_POLICY_VIOLATION | 37 |
| SERVICE_UNAVAILABLE_5XX | 25 |
| OFFICE_CONNECTIVITY_LOSS | 25 |
| SLOW_QUERY_PERFORMANCE | 25 |
| VPN_AUTHENTICATION_FAILURE | 25 |
| DEVICE_COMPLIANCE_FAILURE | 25 |
| AI_PLATFORM_DEGRADATION | 25 |
| CORPORATE_SITE_UNREACHABLE | 25 |
| DISK_SPACE_EXHAUSTION | 25 |
| FIREWALL_RESTRICTION | 25 |
| PROXY_CONFIGURATION | 25 |
| VPN_GATEWAY_CAPACITY | 25 |
| MONITORING_ALERT_NOISE | 25 |
| FAILED_DEPLOYMENT | 25 |
| PASSWORD_EXPIRED_RESET | 25 |
| SERVICE_FAILURE_RESTART | 25 |
| MEMORY_LEAK_CRASH_LOOP | 25 |
| BACKUP_JOB_FAILURE | 25 |
| EXPIRED_VPN_PROFILE | 25 |
| UPDATE_FAILURE_VERSION_CONFLICT | 25 |
| INSTALLATION_FAILURE | 25 |
| MFA_ENROLLMENT_FAILURE | 25 |
| HIGH_LATENCY | 25 |
| ENTERPRISE_APP_MISSING | 25 |
| SSO_SAML_FAILURE | 25 |
| CPU_SATURATION | 25 |
| CONNECTION_POOL_EXHAUSTION | 25 |
| WEBSITE_BLOCKED_BY_FILTER | 25 |
| LAPTOP_PERFORMANCE | 25 |
| APPLICATION_EXECUTION_BLOCKED | 25 |
| DATABASE_LOGIN_FAILURE | 25 |
| APPLICATION_CRASH | 25 |
| ROUTING_PACKET_LOSS | 25 |
| VPN_ROUTE_CONFIGURATION | 25 |
| VPN_MFA_FAILURE | 25 |
| ACCOUNT_LOCKOUT | 25 |
| AI_TOOL_SSO_FAILURE | 25 |
| DNS_RESOLUTION_FAILURE | 25 |
| DEPENDENCY_API_FAILURE | 25 |
| TLS_CERTIFICATE_EXPIRY | 25 |
| PROHIBITED_SITE_CATEGORY | 24 |
| VPN_ACCESS_ENROLLMENT | 24 |
| RESTRICTED_SEGMENT_ACCESS | 24 |
| GROUP_MEMBERSHIP_CHANGE | 23 |
| APPLICATION_ROLE_ASSIGNMENT | 23 |
| INTERNAL_AI_ASSISTANT_ACCESS | 23 |
| AI_DEVELOPMENT_PLATFORM_ACCESS | 23 |
| PRIVILEGED_ACCESS_REQUEST | 23 |
| UNAPPROVED_EXTERNAL_AI_TOOL | 23 |
| DOMAIN_ALLOWLIST_REQUEST | 21 |
| DEVELOPER_TOOLCHAIN_REQUEST | 21 |
| PRODUCTION_DATA_CHANGE_REQUEST | 21 |
| ACCESS_REMOVAL_DEPROVISIONING | 21 |
| PROHIBITED_SOFTWARE_REQUEST | 21 |
| BROWSER_INSTALLATION | 20 |
| SECURITY_CONTROL_EXCEPTION_REQUEST | 20 |
| DEVELOPER_IDE_INSTALLATION | 20 |

## Distribution: priority

| Value | Count |
|---|---|
| Low | 573 |
| Medium | 450 |
| High | 338 |
| Critical | 139 |

## Distribution: root_cause_category

| Value | Count |
|---|---|
| CONFIGURATION_ERROR | 241 |
| MISSING_ENTITLEMENT | 225 |
| POLICY_RESTRICTION | 170 |
| UNDETERMINED | 124 |
| SOFTWARE_DEFECT | 96 |
| DEPENDENCY_FAILURE | 93 |
| USER_ERROR | 74 |
| RESOURCE_EXHAUSTION | 73 |
| EXPIRED_CREDENTIAL | 67 |
| SECURITY_EVENT | 58 |
| CAPACITY_LIMIT | 52 |
| DEPLOYMENT_CHANGE | 51 |
| NETWORK_FAILURE | 50 |
| CERTIFICATE_EXPIRY | 41 |
| VERSION_CONFLICT | 32 |
| DNS_MISCONFIGURATION | 24 |
| HARDWARE_FAULT | 22 |
| FIREWALL_RULE | 7 |

## Distribution: authorization_decision

| Value | Count |
|---|---|
| ALLOW_WITH_APPROVAL | 683 |
| ALLOW | 585 |
| ESCALATE_FOR_REVIEW | 136 |
| DENY | 96 |

## Distribution: approval_status

| Value | Count |
|---|---|
| NOT_REQUIRED | 585 |
| APPROVED | 572 |
| NOT_APPLICABLE | 232 |
| PENDING | 62 |
| REJECTED | 49 |

## Distribution: execution_status

| Value | Count |
|---|---|
| SUCCEEDED | 1022 |
| NOT_EXECUTED | 343 |
| FAILED | 77 |
| ROLLED_BACK | 58 |

## Distribution: initial_verification_result

| Value | Count |
|---|---|
| PASSED | 630 |
| FAILED | 527 |
| NOT_RUN | 343 |

## Distribution: final_verification_result

| Value | Count |
|---|---|
| PASSED | 996 |
| NOT_RUN | 420 |
| FAILED | 84 |

## Distribution: resolution_status

| Value | Count |
|---|---|
| RESOLVED | 814 |
| ESCALATED | 355 |
| REJECTED | 145 |
| RESOLVED_WITH_WORKAROUND | 124 |
| PENDING | 62 |

## Distribution: escalation_required

| Value | Count |
|---|---|
| False | 1145 |
| True | 355 |

## Distribution: scenario_type

| Value | Count |
|---|---|
| success_first_attempt | 258 |
| success_after_approval | 248 |
| failed_then_reinvestigated | 178 |
| escalated_no_permitted_action | 136 |
| incorrect_hypothesis_corrected | 130 |
| insufficient_evidence_provisional_rca | 124 |
| denied_by_policy | 96 |
| exec_success_verification_failed | 84 |
| failed_action_escalated | 77 |
| pending_approval | 62 |
| rollback_after_adverse_effect | 58 |
| approval_rejected | 49 |

## Distribution: environment

| Value | Count |
|---|---|
| CORPORATE_IT | 838 |
| PRODUCTION | 522 |
| STAGING | 100 |
| DEVELOPMENT | 40 |

## Distribution: telemetry_type

| Value | Count |
|---|---|
| REQUEST_RECORD | 375 |
| LOGS | 239 |
| AUTH_EVENT | 173 |
| METRICS | 144 |
| ENDPOINT_EVENT | 134 |
| NETWORK_OBSERVATION | 84 |
| SECURITY_ALERT | 83 |
| VPN_EVENT | 78 |
| APPLICATION_ERROR | 74 |
| HEALTH_CHECK | 68 |
| TRACES | 48 |

## Missing values (tickets)

| Column | Missing | % |
|---|---|---|
| requested_resource | 1125 | 75.0 |
| requested_access_type | 1125 | 75.0 |
| requested_action | 1125 | 75.0 |
| requested_application | 1094 | 72.9 |
| website_or_domain | 1308 | 87.2 |
| ai_tool_name | 1381 | 92.1 |
| business_justification | 1125 | 75.0 |
| requested_duration | 1125 | 75.0 |
| access_scope | 1125 | 75.0 |
| resolved_at | 417 | 27.8 |
| resolution_time_minutes | 417 | 27.8 |
| reinvestigation_trigger | 973 | 64.9 |
| new_evidence | 973 | 64.9 |
| updated_root_cause | 1192 | 79.5 |
| updated_resolution_plan | 1134 | 75.6 |
| approver_role | 735 | 49.0 |
| verification_method | 343 | 22.9 |
| final_action | 1134 | 75.6 |
| final_action_result | 1134 | 75.6 |
| escalation_reason | 1145 | 76.3 |

Null values are intentional where a field does not apply (e.g. request fields on incidents, re-investigation fields when there was none, resolved_at for pending/escalated tickets).