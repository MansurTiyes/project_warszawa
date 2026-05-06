import type { ChatMessage, PlanJSON, PlanVersion, StudentState } from "@/types";
import { inProgressSemester } from "@/lib/schedule-display";
import { SemesterColumn } from "@/components/SemesterColumn";
import { RemarksList } from "@/components/RemarksList";
import { VersionNavigator } from "@/components/VersionNavigator";
import { ChatPanel } from "@/components/ChatPanel";

type Props = {
  current_plan: PlanJSON;
  remarks: string[];
  versions: PlanVersion[];
  currentIndex: number;
  onSelectVersion: (idx: number) => void;
  student_state: StudentState;

  // Chat
  chatMessages: ChatMessage[];
  modify_intent: string | null;
  chatPending: boolean;
  modifyPending: boolean;
  onSend: (text: string) => void;
  onConfirmModify: () => void;
  onRegenerate: () => void;
};

export function ScreenSchedule({
  current_plan,
  remarks,
  versions,
  currentIndex,
  onSelectVersion,
  student_state,
  chatMessages,
  modify_intent,
  chatPending,
  modifyPending,
  onSend,
  onConfirmModify,
  onRegenerate,
}: Props) {
  const semesters = current_plan.semesters ?? [];
  const inProgressIdx = inProgressSemester(current_plan, student_state);

  return (
    <div
      className="print-host"
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <main
        className="print-host"
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          minHeight: 0,
        }}
      >
        <div
          style={{
            flexShrink: 0,
            padding: "40px 56px 26px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <h1
            className="h-display"
            style={{
              fontSize: 36,
              margin: 0,
              letterSpacing: "-0.025em",
            }}
          >
            Your <em>plan</em>
          </h1>
          <button
            type="button"
            className="btn no-print"
            onClick={() => window.print()}
            style={{ padding: "6px 12px", fontSize: 12 }}
          >
            Export PDF
          </button>
        </div>

        <div
          className="print-scroll-fit"
          style={{
            flex: 1,
            overflow: "auto",
            padding: "0 56px 16px",
            minHeight: 0,
          }}
        >
          {semesters.length > 0 ? (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: `repeat(${semesters.length}, 1fr)`,
                gap: 6,
                marginTop: 14,
              }}
            >
              {semesters.map((sem) => (
                <SemesterColumn
                  key={sem.semester_index}
                  semester={sem}
                  inProgress={sem.semester_index === inProgressIdx}
                />
              ))}
            </div>
          ) : (
            <div style={{ color: "var(--ink-3)", fontSize: 14, padding: "32px 0" }}>
              No semesters in this plan.
            </div>
          )}

          <div
            style={{
              marginTop: 36,
              display: "flex",
              alignItems: "flex-start",
              justifyContent: "space-between",
              gap: 32,
            }}
          >
            <div style={{ flex: 1 }}>
              <RemarksList remarks={remarks} />
            </div>
            <div className="no-print">
              <VersionNavigator
                currentIndex={currentIndex}
                count={versions.length}
                onChange={onSelectVersion}
              />
            </div>
          </div>
        </div>

        <ChatPanel
          messages={chatMessages}
          modify_intent={modify_intent}
          pending={chatPending}
          modifyPending={modifyPending}
          onSend={onSend}
          onConfirmModify={onConfirmModify}
          onRegenerate={onRegenerate}
        />
      </main>
    </div>
  );
}
