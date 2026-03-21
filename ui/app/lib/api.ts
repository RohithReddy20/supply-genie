import type {
  ChatCommandResponse,
  ChatMessage,
  ChatMessageResponse,
  IncidentDetail,
  IncidentListResponse,
  KPIDashboard,
  PendingApprovalItem,
} from "./types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export function fetchIncidents(
  status?: string,
  type?: string,
): Promise<IncidentListResponse> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (type) params.set("type", type);
  const qs = params.toString();
  return request(`/incidents${qs ? `?${qs}` : ""}`);
}

export function fetchIncident(id: string): Promise<IncidentDetail> {
  return request(`/incidents/${id}`);
}

export function fetchPendingApprovals(): Promise<{
  items: PendingApprovalItem[];
}> {
  return request("/approvals/pending");
}

export function decideApproval(
  approvalId: string,
  decision: "approved" | "rejected",
  decidedBy: string,
  reason?: string,
): Promise<Record<string, unknown>> {
  return request(`/approvals/${approvalId}/decide`, {
    method: "POST",
    body: JSON.stringify({
      decision,
      decided_by: decidedBy,
      reason: reason ?? "",
    }),
  });
}

export function retryIncident(
  incidentId: string,
): Promise<{ incident_id: string; retried_actions: string[]; count: number }> {
  return request(`/incidents/${incidentId}/retry`, { method: "POST" });
}

// ── KPIs ─────────────────────────────────────────────────────────────

export function fetchKpis(): Promise<KPIDashboard> {
  return request("/kpis");
}

// ── Chat ──────────────────────────────────────────────────────────────

export function sendChatMessage(
  incidentId: string,
  message: string,
  history: Pick<ChatMessage, "role" | "content">[],
): Promise<ChatMessageResponse> {
  return request("/chat/message", {
    method: "POST",
    body: JSON.stringify({
      incident_id: incidentId,
      message,
      history: history.map((m) => ({ role: m.role, content: m.content })),
    }),
  });
}

export function executeChatCommand(
  incidentId: string,
  command: string,
  reason?: string,
): Promise<ChatCommandResponse> {
  return request("/chat/command", {
    method: "POST",
    body: JSON.stringify({
      incident_id: incidentId,
      command,
      reason: reason ?? "",
    }),
  });
}
