import { useEffect, useRef, type RefObject } from "react";
import type { ChatMessage, Me } from "../api";
import { statusLabel } from "../format";
import { Spinner } from "./common";

interface Props {
  me: Me;
  messages: ChatMessage[];
  draft: string;
  setDraft: (text: string) => void;
  sending: boolean;
  waiting: boolean;
  editingEnabled: boolean;
  checking: boolean;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  onSend: (text: string) => void;
  onShowScenario: (id: string) => void;
}

export function Chat({ me, messages, draft, setDraft, sending, waiting, editingEnabled, checking, inputRef, onSend, onShowScenario }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages.length]);

  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const clarification = lastAssistant?.payload.kind === "clarification" ? lastAssistant.payload.clarification : null;

  return (
    <section className="panel panel-center" aria-labelledby="chat-heading">
      <div className="chat-header">
        <h2 id="chat-heading">Conversation</h2>
        {me.ai_mode === "demo" ? (
          <span className="badge badge-demo" title="No AI is used in demo mode">
            Demo mode: scripted responses, not AI
          </span>
        ) : (
          <span className="badge badge-ai">AI assistant (Claude{me.ai_model ? `, ${me.ai_model}` : ""})</span>
        )}
      </div>
      {me.ai_mode === "anthropic" && <p className="privacy small">{me.privacy_notice}</p>}
      {checking && (
        <p className="notice small" role="status">
          Checking that the workbook recalculates correctly. You can send messages as soon as this finishes.
        </p>
      )}
      {!editingEnabled && !checking && (
        <p className="notice small">Editing is not enabled for this workbook yet, so scenarios cannot be calculated. You can still ask about it.</p>
      )}

      <div className="chat-log" role="log" aria-live="polite" aria-relevant="additions">
        {messages.length === 0 && (
          <div className="suggestions">
            <p className="muted">Try asking:</p>
            <ul className="plain-list">
              {me.suggested_prompts.map((p) => (
                <li key={p}>
                  <button type="button" className="suggestion" onClick={() => onSend(p)} disabled={sending || checking}>
                    {p}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {messages.map((m) => (
          <article key={m.id} className={`msg msg-${m.role}${m.payload.kind === "error" ? " msg-error" : ""}`}>
            <header className="msg-meta small">
              {m.role === "user" ? "You" : m.payload.mode === "demo" ? "Assistant (scripted demo)" : "Assistant"}
              {m.payload.kind === "error" && <span className="badge badge-failed">Not completed</span>}
            </header>
            <div className="msg-text">{m.text}</div>
            {m.payload.kind === "clarification" && m.payload.clarification && (
              <p className="small muted">Question: {m.payload.clarification.question}</p>
            )}
            {m.payload.proposed_rule && (
              <p className="small notice">
                Suggested business rule (not saved): “{m.payload.proposed_rule.text}”. An owner can add it in “Understand this workbook”.
              </p>
            )}
            {m.payload.scenario && (
              <button type="button" className="small" onClick={() => onShowScenario(m.payload.scenario!.id)}>
                View scenario “{m.payload.scenario.name}” ({statusLabel(m.payload.scenario.status)})
              </button>
            )}
            {m.payload.error?.request_id && <p className="small muted">Reference: {m.payload.error.request_id}</p>}
          </article>
        ))}
        {(sending || waiting) && (
          <p className="msg msg-assistant">
            <Spinner label={waiting ? "Recalculating the workbook…" : "Working on it…"} />
          </p>
        )}
        <div ref={endRef} />
      </div>

      {clarification && clarification.options.length > 0 && !sending && !checking && (
        <div className="clarify" role="group" aria-label="Answer choices">
          {clarification.options.map((o) => (
            <button key={o} type="button" className="choice" onClick={() => onSend(o)}>
              {o}
            </button>
          ))}
        </div>
      )}

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          if (draft.trim() && !checking) onSend(draft.trim());
        }}
      >
        <label htmlFor="chat-input" className="visually-hidden">
          Message
        </label>
        <textarea
          id="chat-input"
          ref={inputRef}
          value={draft}
          rows={3}
          maxLength={4000}
          placeholder="Describe what you want to explore, e.g. “What if packaging wages rise 5%?”"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (draft.trim() && !sending && !checking) onSend(draft.trim());
            }
          }}
        />
        <button type="submit" className="primary" disabled={sending || checking || !draft.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
