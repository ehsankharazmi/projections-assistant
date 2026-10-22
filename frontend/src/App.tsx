import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage, Me, Scenario, ScenarioListItem, VersionInfo, WorkbookListItem, WorkbookSummary } from "./api";
import { ApiError, api, newKey, sleep, waitForJob } from "./api";
import { Chat } from "./components/Chat";
import { ErrorNotice, Spinner, toUiError, type UiError } from "./components/common";
import { LeftPanel } from "./components/LeftPanel";
import { ResultsPanel } from "./components/ResultsPanel";
import { CompatibilityReview, SetupScreen } from "./components/SetupScreen";
import { UsageView } from "./components/UsageView";
import { WorkbookPicker } from "./components/WorkbookPicker";

type View = "picker" | "chat" | "setup" | "usage";
const CALCULATING = ["draft", "validating", "calculating"];

function mergeMessages(existing: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const byId = new Map(existing.map((m) => [m.id, m]));
  for (const m of incoming) byId.set(m.id, m);
  return [...byId.values()].sort((a, b) => a.seq - b.seq);
}

function latestScenarioId(messages: ChatMessage[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const id = messages[i].payload.scenario?.id ?? messages[i].payload.scenario_id;
    if (id) return id;
  }
  return null;
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [fatal, setFatal] = useState<UiError | null>(null);
  const [error, setError] = useState<UiError | null>(null);
  const [status, setStatus] = useState("");
  const [view, setView] = useState<View>("picker");
  const [busy, setBusy] = useState<string | null>(null);
  const [workbooks, setWorkbooks] = useState<WorkbookListItem[]>([]);
  const [workbook, setWorkbook] = useState<WorkbookSummary | null>(null);
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [scenarios, setScenarios] = useState<ScenarioListItem[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pendingTurns, setPendingTurns] = useState(false);
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const workbookId = workbook?.id ?? null;

  const announce = (text: string) => setStatus(text);
  const fail = (e: unknown) => setError(toUiError(e));

  const refreshList = useCallback(async () => {
    setWorkbooks(await api.listWorkbooks());
  }, []);

  useEffect(() => {
    Promise.all([api.me(), api.listWorkbooks()]).then(
      ([m, list]) => {
        setMe(m);
        setWorkbooks(list);
      },
      (e) => setFatal(toUiError(e)),
    );
  }, []);

  const refreshWorkbook = useCallback(async (id: string) => {
    const [wb, vs, ss] = await Promise.all([api.getWorkbook(id), api.versions(id), api.listScenarios(id)]);
    setWorkbook(wb);
    setVersions(vs);
    setScenarios(ss);
    return wb;
  }, []);

  const selectScenario = useCallback(async (id: string) => {
    try {
      setScenario(await api.getScenario(id));
    } catch (e) {
      fail(e);
    }
  }, []);

  const openWorkbook = useCallback(
    async (id: string, nextView: View = "chat") => {
      setError(null);
      try {
        await refreshWorkbook(id);
        const res = await api.messages(id, 0);
        setMessages(res.messages);
        setPendingTurns(res.pending_turns.length > 0);
        const sid = latestScenarioId(res.messages);
        setScenario(null);
        if (sid) await selectScenario(sid);
        setView(nextView);
      } catch (e) {
        fail(e);
      }
    },
    [refreshWorkbook, selectScenario],
  );

  const afterImport = async (result: { workbook: WorkbookSummary; job_id: string | null }, demo: boolean) => {
    const id = result.workbook.id;
    await openWorkbook(id, demo ? "chat" : "setup");
    if (result.job_id) {
      setBusy("Checking that the workbook recalculates correctly…");
      announce("Checking compatibility");
      const job = await waitForJob(result.job_id);
      announce(job.status === "succeeded" ? "Compatibility check finished" : "Compatibility check could not be completed");
    }
    const wb = await refreshWorkbook(id);
    setBusy(null);
    await refreshList();
    if (wb.compatibility_status !== "editable") setView("setup");
  };

  const loadDemo = async () => {
    setBusy("Creating the demo workbook…");
    setError(null);
    try {
      await afterImport(await api.loadDemo(), true);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  const upload = async (file: File) => {
    setBusy("Uploading and inspecting…");
    setError(null);
    try {
      await afterImport(await api.upload(file), false);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  const deleteWorkbook = async (wb: WorkbookListItem) => {
    if (!window.confirm(`Delete “${wb.name}”, all its versions, scenarios and conversation? This cannot be undone.`)) return;
    try {
      await api.deleteWorkbook(wb.id);
      announce(`Deleted ${wb.name}`);
      if (workbookId === wb.id) {
        setWorkbook(null);
        setView("picker");
      }
      await refreshList();
    } catch (e) {
      fail(e);
    }
  };

  const send = async (text: string) => {
    if (!workbookId || sending) return;
    setSending(true);
    setError(null);
    setDraft("");
    try {
      const res = await api.sendMessage(workbookId, text, newKey());
      setMessages((ms) => mergeMessages(ms, res.messages));
      if (res.turn.status === "awaiting_job" || res.turn.status === "running") setPendingTurns(true);
      const sid = latestScenarioId(res.messages);
      if (sid) await selectScenario(sid);
      await refreshWorkbook(workbookId);
      announce("Assistant replied");
    } catch (e) {
      setDraft(text);
      fail(e);
    } finally {
      setSending(false);
    }
  };

  // Poll for messages while a turn is waiting on a calculation.
  useEffect(() => {
    if (!pendingTurns || !workbookId) return;
    let cancelled = false;
    (async () => {
      while (!cancelled) {
        await sleep(2000);
        if (cancelled) return;
        try {
          const last = messages.length ? messages[messages.length - 1].seq : 0;
          const res = await api.messages(workbookId, last);
          if (cancelled) return;
          if (res.messages.length) {
            setMessages((ms) => mergeMessages(ms, res.messages));
            const sid = latestScenarioId(res.messages);
            if (sid) await selectScenario(sid);
          }
          if (res.pending_turns.length === 0) {
            setPendingTurns(false);
            await refreshWorkbook(workbookId);
            return;
          }
        } catch {
          /* transient polling failure: try again */
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pendingTurns, workbookId, messages, refreshWorkbook, selectScenario]);

  // Poll a scenario while it is calculating.
  useEffect(() => {
    if (!scenario || !CALCULATING.includes(scenario.status)) return;
    const timer = setTimeout(() => {
      api.getScenario(scenario.id).then(
        (s) => {
          setScenario(s);
          if (!CALCULATING.includes(s.status) && workbookId) void refreshWorkbook(workbookId);
        },
        () => undefined,
      );
    }, 1500);
    return () => clearTimeout(timer);
  }, [scenario, workbookId, refreshWorkbook]);

  const applyScenario = async (s: Scenario, key: string) => {
    if (!workbook?.active_version) return;
    setBusy("Applying…");
    setError(null);
    try {
      const res = await api.applyScenario(s.id, workbook.active_version.id, key);
      announce(`Applied. Version ${res.version.seq} is now active.`);
      await refreshWorkbook(workbook.id);
      await selectScenario(s.id);
    } catch (e) {
      if (e instanceof ApiError && e.body.code === "stale_preview") {
        setError({ message: `${e.body.message} Ask the assistant again (for example “recalculate that”) to get a fresh preview.`, code: e.body.code, diagnosticId: e.body.diagnostic_id });
      } else {
        fail(e);
      }
      await selectScenario(s.id);
    } finally {
      setBusy(null);
    }
  };

  const discardScenario = async (s: Scenario) => {
    try {
      await api.discardScenario(s.id);
      announce("Scenario discarded");
      await selectScenario(s.id);
      if (workbookId) await refreshWorkbook(workbookId);
    } catch (e) {
      fail(e);
    }
  };

  const revise = () => {
    setView("chat");
    setDraft("Make that ");
    setTimeout(() => {
      const el = inputRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(el.value.length, el.value.length);
      }
    }, 0);
  };

  const restore = async (v: VersionInfo) => {
    if (!workbook?.active_version) return;
    if (!window.confirm(`Make version ${v.seq} (“${v.label}”) the active version again? Later versions stay in the history.`)) return;
    try {
      await api.restore(workbook.id, v.id, workbook.active_version.id);
      announce(`Version ${v.seq} restored`);
      await refreshWorkbook(workbook.id);
      if (scenario) await selectScenario(scenario.id);
    } catch (e) {
      fail(e);
    }
  };

  if (fatal) {
    return (
      <main className="app-fatal">
        <h1>Workbook Assistant</h1>
        <ErrorNotice error={fatal} />
      </main>
    );
  }
  if (!me) {
    return (
      <main className="app-fatal">
        <Spinner label="Loading…" />
      </main>
    );
  }

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="topbar">
        <span className="app-title">Workbook Assistant</span>
        <nav aria-label="Main">
          <button type="button" className={view === "picker" ? "nav selected" : "nav"} onClick={() => { setView("picker"); void refreshList(); }}>
            Workbooks
          </button>
          {workbook && (
            <>
              <button type="button" className={view === "chat" ? "nav selected" : "nav"} onClick={() => setView("chat")}>
                Conversation
              </button>
              <button type="button" className={view === "setup" ? "nav selected" : "nav"} onClick={() => setView("setup")}>
                Understand this workbook
              </button>
            </>
          )}
          {me.is_owner && (
            <button type="button" className={view === "usage" ? "nav selected" : "nav"} onClick={() => setView("usage")}>
              Usage
            </button>
          )}
        </nav>
        <span className="small muted">{me.email}</span>
      </header>
      <div className="visually-hidden" role="status" aria-live="polite">
        {status}
      </div>
      <main id="main" className={view === "chat" && workbook ? "workspace" : "single"}>
        {error && <ErrorNotice error={error} onDismiss={() => setError(null)} />}
        {view === "picker" && (
          <WorkbookPicker workbooks={workbooks} busy={busy} onOpen={(id) => void openWorkbook(id)} onLoadDemo={loadDemo} onUpload={upload} onDelete={deleteWorkbook} />
        )}
        {view === "usage" && <UsageView onClose={() => setView(workbook ? "chat" : "picker")} />}
        {view === "setup" && workbook && (
          <>
            {busy && (
              <p className="notice">
                <Spinner label={busy} />
              </p>
            )}
            <SetupScreen me={me} workbook={workbook} onChanged={() => void refreshWorkbook(workbook.id)} onClose={() => setView("chat")} />
          </>
        )}
        {view === "chat" && workbook && (
          <>
            <LeftPanel
              workbook={workbook}
              versions={versions}
              scenarios={scenarios}
              selectedScenarioId={scenario?.id ?? null}
              busy={busy !== null}
              onSelectScenario={(id) => void selectScenario(id)}
              onRestore={restore}
            />
            <div className="center-col">
              {workbook.compatibility_status !== "editable" && <CompatibilityReview workbook={workbook} />}
              <Chat
                me={me}
                messages={messages}
                draft={draft}
                setDraft={setDraft}
                sending={sending}
                waiting={pendingTurns}
                editingEnabled={workbook.editing_enabled}
                checking={workbook.compatibility_status === "pending"}
                inputRef={inputRef}
                onSend={(t) => void send(t)}
                onShowScenario={(id) => void selectScenario(id)}
              />
            </div>
            <ResultsPanel workbook={workbook} scenario={scenario} busy={busy !== null} onApply={applyScenario} onRevise={revise} onDiscard={discardScenario} newKey={newKey} />
          </>
        )}
      </main>
    </div>
  );
}
