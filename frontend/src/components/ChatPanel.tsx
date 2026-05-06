import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { ChatMessage } from "@/types";

type Props = {
  messages: ChatMessage[];
  modify_intent: string | null;
  pending: boolean;
  modifyPending: boolean;
  onSend: (text: string) => void;
  onConfirmModify: () => void;
  onRegenerate: () => void;
};

export function ChatPanel({
  messages,
  modify_intent,
  pending,
  modifyPending,
  onSend,
  onConfirmModify,
  onRegenerate,
}: Props) {
  const [input, setInput] = useState("");
  const tailRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    tailRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, modify_intent, modifyPending]);

  const sendDisabled = pending || modifyPending || input.trim().length === 0;

  function submit() {
    const text = input.trim();
    if (!text || pending || modifyPending) return;
    setInput("");
    onSend(text);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  const lastAiIndex = lastIndexOf(messages, "ai");

  return (
    <div
      className="no-print"
      style={{
        flexShrink: 0,
        borderTop: "1px solid var(--line-1)",
        background: "var(--bg-1)",
        padding: "20px 56px 20px",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 14,
          marginBottom: 16,
          maxHeight: 220,
          overflow: "auto",
        }}
      >
        {messages.map((m, i) => {
          const showModifyButtons =
            i === lastAiIndex && modify_intent != null && !modifyPending;
          return m.type === "ai" ? (
            <AiBubble
              key={i}
              content={m.content}
              showModifyButtons={showModifyButtons}
              onConfirmModify={onConfirmModify}
              onRegenerate={onRegenerate}
            />
          ) : (
            <UserBubble key={i} content={m.content} />
          );
        })}
        {pending && <PendingBubble />}
        {modifyPending && <ModifyPendingBubble />}
        <div ref={tailRef} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          background: "var(--bg-2)",
          border: "1px solid var(--line-2)",
          borderRadius: 10,
          padding: "12px 16px",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--ink-3)",
          }}
        >
          ›
        </span>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your plan, swap a course, or check a policy…"
          rows={1}
          disabled={pending || modifyPending}
          style={{
            flex: 1,
            background: "transparent",
            border: "none",
            outline: "none",
            color: "var(--ink-0)",
            fontFamily: "var(--font-sans)",
            fontSize: 14,
            resize: "none",
            lineHeight: 1.4,
            padding: 0,
          }}
        />
        <button
          type="submit"
          disabled={sendDisabled}
          aria-label="Send"
          style={{
            width: 28,
            height: 28,
            borderRadius: 6,
            background: "var(--cardinal-bright)",
            border: "none",
            color: "white",
            cursor: sendDisabled ? "default" : "pointer",
            display: "grid",
            placeItems: "center",
            fontSize: 14,
            opacity: sendDisabled ? 0.45 : 1,
          }}
        >
          ↑
        </button>
      </form>
    </div>
  );
}

// ---- Subcomponents ----

function AiAvatar() {
  return (
    <div
      style={{
        width: 22,
        height: 22,
        flexShrink: 0,
        borderRadius: 4,
        background: "var(--cardinal-bright)",
        display: "grid",
        placeItems: "center",
        fontFamily: "var(--font-serif)",
        fontStyle: "italic",
        fontSize: 12,
        fontWeight: 600,
        color: "white",
      }}
    >
      A
    </div>
  );
}

function AiBubble({
  content,
  showModifyButtons,
  onConfirmModify,
  onRegenerate,
}: {
  content: string;
  showModifyButtons: boolean;
  onConfirmModify: () => void;
  onRegenerate: () => void;
}) {
  return (
    <div style={{ display: "flex", gap: 12 }}>
      <AiAvatar />
      <div style={{ maxWidth: 880 }}>
        <div
          style={{
            fontSize: 14,
            lineHeight: 1.55,
            color: "var(--ink-1)",
            whiteSpace: "pre-wrap",
            marginBottom: showModifyButtons ? 10 : 0,
          }}
        >
          {content}
        </div>
        {showModifyButtons && (
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className="btn btn--primary"
              style={{ padding: "8px 14px", fontSize: 13 }}
              onClick={onConfirmModify}
            >
              ✓ Confirm plan
            </button>
            <button
              type="button"
              className="btn"
              style={{ padding: "8px 14px", fontSize: 13 }}
              onClick={onRegenerate}
            >
              ↻ Regenerate
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => window.print()}
              style={{ padding: "8px 14px", fontSize: 13 }}
            >
              Export PDF
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function UserBubble({ content }: { content: string }) {
  return (
    <div style={{ display: "flex", gap: 12, justifyContent: "flex-end" }}>
      <div
        style={{
          fontSize: 14,
          lineHeight: 1.55,
          color: "var(--ink-0)",
          background: "var(--bg-3)",
          padding: "10px 14px",
          borderRadius: 12,
          maxWidth: 560,
          whiteSpace: "pre-wrap",
        }}
      >
        {content}
      </div>
    </div>
  );
}

function PendingBubble() {
  return (
    <div style={{ display: "flex", gap: 12 }}>
      <AiAvatar />
      <div
        style={{
          fontSize: 14,
          lineHeight: 1.55,
          color: "var(--ink-3)",
          fontStyle: "italic",
        }}
      >
        Thinking…
      </div>
    </div>
  );
}

function ModifyPendingBubble() {
  return (
    <div style={{ display: "flex", gap: 12 }}>
      <AiAvatar />
      <div
        style={{
          fontSize: 14,
          lineHeight: 1.55,
          color: "var(--ink-3)",
          fontStyle: "italic",
        }}
      >
        Updating your plan…
      </div>
    </div>
  );
}

function lastIndexOf(messages: ChatMessage[], type: ChatMessage["type"]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].type === type) return i;
  }
  return -1;
}
