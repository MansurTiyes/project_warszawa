import type { PlanJSON, PlanVersion, StudentState } from "@/types";
import { inProgressSemester } from "@/lib/schedule-display";
import { SemesterColumn } from "@/components/SemesterColumn";
import { RemarksList } from "@/components/RemarksList";
import { VersionNavigator } from "@/components/VersionNavigator";

type Props = {
  current_plan: PlanJSON;
  remarks: string[];
  versions: PlanVersion[];
  currentIndex: number;
  onSelectVersion: (idx: number) => void;
  student_state: StudentState;
};

export function ScreenSchedule({
  current_plan,
  remarks,
  versions,
  currentIndex,
  onSelectVersion,
  student_state,
}: Props) {
  const semesters = current_plan.semesters ?? [];
  const inProgressIdx = inProgressSemester(current_plan, student_state);

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <main
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          style={{
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
            className="btn"
            disabled
            style={{ padding: "6px 12px", fontSize: 12, opacity: 0.5, cursor: "default" }}
          >
            Export PDF
          </button>
        </div>

        <div style={{ flex: 1, padding: "0 56px 40px" }}>
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
            <VersionNavigator
              currentIndex={currentIndex}
              count={versions.length}
              onChange={onSelectVersion}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
