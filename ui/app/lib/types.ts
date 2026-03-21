export type IncidentType = "shipment_delay" | "worker_absence";
export type IncidentStatus = "open" | "in_progress" | "resolved" | "escalated";
export type Severity = "low" | "medium" | "high" | "critical";
export type ActionType =
  | "slack_notify"
  | "call_production"
  | "call_contractor"
  | "update_po"
  | "update_labor"
  | "email_customer"
  | "notify_manager"
  | "escalate_ticket";
export type ActionStatus =
  | "pending"
  | "queued"
  | "in_progress"
  | "completed"
  | "failed"
  | "needs_approval"
  | "skipped";
export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface Approval {
  id: string;
  status: ApprovalStatus;
  requested_at: string;
  decided_at: string | null;
  decided_by: string | null;
  reason: string | null;
}

export interface ActionRun {
  id: string;
  action_type: ActionType;
  status: ActionStatus;
  sequence: number;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  retry_count: number;
  approval: Approval | null;
}

export interface IncidentSummary {
  id: string;
  idempotency_key: string;
  type: IncidentType;
  status: IncidentStatus;
  severity: Severity;
  correlation_id: string;
  created_at: string;
}

export interface IncidentDetail extends IncidentSummary {
  source: string;
  payload: Record<string, unknown> | null;
  resolved_at: string | null;
  updated_at: string;
  actions: ActionRun[];
}

export interface IncidentListResponse {
  items: IncidentSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface PendingApprovalItem {
  id: string;
  action_run_id: string;
  incident_id: string;
  action_type: string;
  status: string;
  requested_at: string;
  context: Record<string, unknown> | null;
}

// ── Chat types ──────────────────────────────────────────────────────────

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  proposed_actions?: ProposedAction[];
}

export interface ProposedAction {
  action_type: string;
  label: string;
  description: string;
  requires_approval: boolean;
}

export interface ChatMessageResponse {
  reply: string;
  proposed_actions: ProposedAction[];
}

export interface ChatCommandResponse {
  status: string;
  action_run_id: string | null;
  message: string;
}

// ── KPI types ────────────────────────────────────────────────────────────

export interface IncidentKPIs {
  total: number;
  by_status: Record<string, number>;
  by_type: Record<string, number>;
  auto_resolution_rate: number;
  escalation_rate: number;
  mean_time_to_resolution_s: number | null;
}

export interface ActionKPIs {
  total: number;
  completed: number;
  failed: number;
  pending: number;
  needs_approval: number;
  success_rate: number;
  failure_rate: number;
  avg_duration_ms: number | null;
}

export interface ActionTypeBreakdown {
  action_type: string;
  total: number;
  completed: number;
  failed: number;
  success_rate: number;
  avg_duration_ms: number | null;
}

export interface VoiceKPIs {
  total_sessions: number;
  completed_sessions: number;
  answer_rate: number;
  avg_duration_s: number | null;
  total_duration_s: number;
}

export interface KPIDashboard {
  incidents: IncidentKPIs;
  actions: ActionKPIs;
  action_breakdown: ActionTypeBreakdown[];
  voice: VoiceKPIs;
  generated_at: string;
}
