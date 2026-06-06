"""
ASG Solutions — Database Package
=================================
This file makes imports cleaner throughout the project.
Instead of:  from aurora_shared.database.connection import SessionLocal
You can do:  from aurora_shared.database import SessionLocal
"""

from aurora_shared.database.connection import engine, SessionLocal, Base, get_db, create_tables, get_engine
from aurora_shared.database.models import (
    Business,
    Invoice,
    ActionLog,
    User,
    Payment,
    TelegramSession,
    WhatsAppSession,
    WhatsAppOutboundLog,
    # Sprint 1 — Identity Foundation
    Organization,
    Membership,
    AccountantEngagement,
    Invitation,
    # Aurora Onboarding Module — Phase 6b
    OnboardingState,
    OtpVerification,
    KycDocument,
    Subscription,
    PaymentMethod,
    SubscriptionPayment,
    # Sprint 2 — Document AI Receipt Pipeline (Phase 7)
    Receipt,
    Expense,
    # Sprint 3 — Real ITA client + Secret Manager (Phase 8)
    ItaAuditLog,
    # Sprint 4 — Accountant Channel + Exports (Phase 9)
    Export,
    AccountantCoaMapping,
    # Sprint 5 — Revenue Share Engine (Phase 10)
    RevenueShareLedger,
    AccountantPayout,
    AccountantReferral,
    # Sprint 6 — Hardening (Phase 11)
    AuditExportCursor,
    # Sprint 7 — Marketing + v2.0 Virtual Tax Shield (Phase 12)
    MarketingLead,
    TaxObligation,
    VirtualLedger,
    VirtualBalance,
    RemittanceLink,
    PaymentConfirmation,
    # Track 3 — Break-glass Tier 1.5 (Phase 13)
    BreakGlassToken,
    # Appendix H — Tier 1 CEO Executive Dashboard (Phase 14)
    VerticalTemplate,
    ExecEvent,
    # Appendix I Sprint 2 — Categories + Session Snapshots + WebAuthn (Phase 15)
    BusinessCategory,
    CeoSessionSnapshot,
    WebauthnCredential,
    # Appendix J Sprint 3 — AI Copilot Console (Phase 16)
    CopilotConversation,
    CopilotMessage,
    CopilotProvisioningRun,
    ClaudeApiUsage,
    # Appendix L Sprint 4 — Vertex AI / Gemini multi-workload (Phase 17)
    GeminiRun,
    DailyBriefCard,
    # Appendix M Sprint 5 — Pre-Armed Autonomous Architecture (Phase 18)
    ProjectConstraint,
    HcarlPolicyState,
    CausalInsight,
    FederatedSyncLog,
    GrowthMilestone,
    # Sprint 8.2 — Aurora Mac Shell hardware binding (Phase 20)
    NativeDeviceKey,
    NativeHandshakeChallenge,
    # Sprint 8.2 sibling — Accountant Portal auth (Phase 21)
    AccountantDevice,
    AccountantRefreshToken,
    AccountantOtpAttempt,
    # Sprint 8.3 — Document Vault DB Layer (Phase 21 vault)
    ClientDocument,
    VaultIngestionAddress,
    # P2-08 — AML / Sanctions Screening (Phase 22)
    SanctionsListEntry,
    SanctionsScreeningHit,
    # P2-20 — Predictive Anomaly Detection (Phase 23)
    AnomalyEvent,
    # P2-22 — VAT Return Filing (Phase 24)
    VatReturn,
    # P2-23 — Payment Links (Phase 25)
    PaymentLink,
)
