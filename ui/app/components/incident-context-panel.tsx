"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { decideApproval, retryIncident } from "@/app/lib/api";
import { IncidentStatusBadge, SeverityBadge } from "./status-badge";
import { ActionTimeline } from "./action-timeline";
import type { IncidentDetail } from "@/app/lib/types";
import {
  Truck,
  UserX,
  Clock,
  RotateCcw,
  Copy,
  Check,
} from "lucide-react";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

export function IncidentContextPanel({
  incident,
  onRefresh,
}: {
  incident: IncidentDetail;
  onRefresh: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [retrying, setRetrying] = useState(false);

  const isDelay = incident.type === "shipment_delay";
  const TypeIcon = isDelay ? Truck : UserX;
  const hasFailedActions = incident.actions.some((a) => a.status === "failed");

  const handleApprove = async (approvalId: string) => {
    await decideApproval(approvalId, "approved", "operator@console", "Approved via console");
    onRefresh();
  };

  const handleReject = async (approvalId: string) => {
    await decideApproval(approvalId, "rejected", "operator@console", "Rejected via console");
    onRefresh();
  };

  const handleRetry = async () => {
    setRetrying(true);
    try {
      await retryIncident(incident.id);
      onRefresh();
    } finally {
      setRetrying(false);
    }
  };

  const copyCorrelationId = () => {
    navigator.clipboard.writeText(incident.correlation_id);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-5 pt-4 pb-3 flex-shrink-0">
        <div className="flex items-start justify-between">
          <div className="flex items-start gap-2.5">
            <div className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center flex-shrink-0">
              <TypeIcon className="w-4.5 h-4.5 text-muted-foreground" />
            </div>
            <div>
              <h2 className="font-heading text-lg font-medium tracking-tight">
                {isDelay ? "Shipment Delay" : "Worker Absence"}
              </h2>
              <div className="flex items-center gap-1.5 mt-0.5">
                <IncidentStatusBadge status={incident.status} />
                <SeverityBadge severity={incident.severity} />
              </div>
            </div>
          </div>

          {hasFailedActions && (
            <button
              onClick={handleRetry}
              disabled={retrying}
              className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium bg-white border border-border rounded-md hover:bg-muted transition-colors disabled:opacity-50"
            >
              <RotateCcw
                className={`w-3 h-3 ${retrying ? "animate-spin" : ""}`}
              />
              Retry
            </button>
          )}
        </div>

        {/* Meta */}
        <div className="flex items-center gap-3 mt-2.5 text-[11px] text-muted-foreground">
          <Tooltip>
            <TooltipTrigger
              onClick={copyCorrelationId}
              className="flex items-center gap-1 hover:text-foreground transition-colors font-mono cursor-pointer"
            >
              {copied ? (
                <Check className="w-3 h-3 text-[#5c6b55]" />
              ) : (
                <Copy className="w-3 h-3" />
              )}
              {incident.correlation_id.slice(0, 8)}
            </TooltipTrigger>
            <TooltipContent>Copy correlation ID</TooltipContent>
          </Tooltip>

          <div className="flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatDate(incident.created_at)}
          </div>
        </div>
      </div>

      <Separator />

      {/* Scrollable content */}
      <div className="flex-1 overflow-auto px-5 py-4">
        {/* Payload context card */}
        {incident.payload && (
          <Card className="mb-4 bg-warm border-border shadow-none">
            <CardHeader className="pb-1.5 pt-3 px-3.5">
              <p className="text-[10px] font-medium tracking-widest uppercase text-muted-foreground">
                Event Context
              </p>
            </CardHeader>
            <CardContent className="px-3.5 pb-3">
              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
                {Object.entries(incident.payload).map(([key, value]) => (
                  <div key={key}>
                    <dt className="text-[11px] text-muted-foreground">
                      {key.replace(/_/g, " ")}
                    </dt>
                    <dd className="text-xs font-medium mt-0.5">
                      {String(value)}
                    </dd>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Action timeline */}
        <div>
          <div className="flex items-center gap-2 mb-3">
            <div className="w-5 h-5 rounded bg-[#5c6b55] flex items-center justify-center">
              <span className="text-[9px] font-bold text-white">W</span>
            </div>
            <h3 className="text-xs font-medium">Supply chain coordinator</h3>
            <span className="text-[11px] text-muted-foreground">
              HappyRobot AI worker
            </span>
          </div>

          <ActionTimeline
            actions={incident.actions}
            onApprove={handleApprove}
            onReject={handleReject}
          />
        </div>
      </div>
    </div>
  );
}
