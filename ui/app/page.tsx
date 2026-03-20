"use client";

import { useState, useEffect, useCallback } from "react";
import { Separator } from "@/components/ui/separator";
import { IncidentList } from "./components/incident-list";
import { IncidentContextPanel } from "./components/incident-context-panel";
import { ChatPanel } from "./components/chat-panel";
import { fetchIncident } from "./lib/api";
import type { IncidentDetail } from "./lib/types";
import {
  Truck,
  Radio,
  Users,
  Phone,
  Settings,
  Search,
  BarChart3,
  Bell,
} from "lucide-react";

export default function Home() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [incident, setIncident] = useState<IncidentDetail | null>(null);

  const loadIncident = useCallback(async () => {
    if (!selectedId) {
      setIncident(null);
      return;
    }
    try {
      const data = await fetchIncident(selectedId);
      setIncident(data);
    } catch {
      /* ignore */
    }
  }, [selectedId]);

  useEffect(() => {
    loadIncident();
    const interval = setInterval(loadIncident, 3000);
    return () => clearInterval(interval);
  }, [loadIncident]);

  return (
    <div className="flex flex-col h-screen">
      {/* ── Header ── */}
      <header className="flex items-center justify-between px-5 h-12 border-b border-border bg-card flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-md bg-[#5c6b55] flex items-center justify-center">
            <span className="text-sm font-bold text-white tracking-tight">
              H
            </span>
          </div>
          <span className="font-heading text-[15px] font-medium tracking-tight">
            HappyRobot
          </span>
          <span className="text-xs text-muted-foreground">▾</span>
        </div>

        {/* Toolbar icons */}
        <div className="flex items-center gap-1">
          {[Users, Phone, BarChart3, Bell, Search, Settings].map(
            (Icon, i) => (
              <button
                key={i}
                className="p-2 rounded-md hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
              >
                <Icon className="w-4 h-4" />
              </button>
            ),
          )}
          <Separator orientation="vertical" className="h-5 mx-1" />
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground ml-1">
            <Radio className="w-3.5 h-3.5 text-[#5c6b55]" />
            <span>Live</span>
          </div>
          <div className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-muted text-xs font-medium text-muted-foreground ml-2">
            <Truck className="w-3.5 h-3.5" />
            EW Workspace
          </div>
        </div>
      </header>

      {/* ── Main workspace: 3 columns ── */}
      <div className="flex flex-1 min-h-0">
        {/* Left: incident list */}
        <aside className="w-[300px] border-r border-border bg-card flex-shrink-0 flex flex-col">
          <IncidentList selectedId={selectedId} onSelect={setSelectedId} />
        </aside>

        {selectedId && incident ? (
          <>
            {/* Center: chat (primary) */}
            <main className="flex-1 min-w-0 flex flex-col bg-card">
              <ChatPanel
                incidentId={selectedId}
                onActionExecuted={loadIncident}
              />
            </main>

            {/* Right: incident context + action timeline */}
            <aside className="w-[380px] border-l border-border bg-background flex-shrink-0 flex flex-col">
              <IncidentContextPanel
                incident={incident}
                onRefresh={loadIncident}
              />
            </aside>
          </>
        ) : (
          <main className="flex-1 min-w-0 bg-background">
            <EmptyState />
          </main>
        )}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-8">
      <div className="w-16 h-16 rounded-2xl bg-muted flex items-center justify-center mb-4">
        <Truck className="w-8 h-8 text-muted-foreground/50" />
      </div>
      <h2 className="font-heading text-2xl font-medium tracking-tight mb-2">
        Supply Chain Coordinator
      </h2>
      <p className="text-sm text-muted-foreground max-w-md leading-relaxed">
        Select an incident from the sidebar to start working with the AI
        coordinator.
      </p>
    </div>
  );
}
