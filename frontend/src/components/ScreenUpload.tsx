import { useRef, useState } from "react";
import { validatePdf } from "@/lib/pdf-validate";

type Props = {
  onSelect: (file: File) => void;
  initialError?: string | null;
};

export function ScreenUpload({ onSelect, initialError = null }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(initialError);
  const [dragOver, setDragOver] = useState(false);

  async function handleFile(file: File) {
    const result = await validatePdf(file);
    if (!result.ok) {
      setError(result.reason);
      return;
    }
    setError(null);
    onSelect(file);
  }

  function onClickChoose() {
    inputRef.current?.click();
  }

  function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) void handleFile(file);
    e.target.value = "";
  }

  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void handleFile(file);
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: "0 80px",
      }}
    >
      <div style={{ width: 520, textAlign: "center" }}>
        <h1
          className="h-display"
          style={{ fontSize: 38, margin: "0 0 12px", letterSpacing: "-0.025em" }}
        >
          Drop your <em>STARS</em> report here
        </h1>
        <p
          style={{
            fontSize: 15,
            color: "var(--ink-2)",
            margin: "0 0 40px",
            lineHeight: 1.5,
          }}
        >
          We'll parse it in your browser. Nothing leaves the page.
        </p>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          style={{
            position: "relative",
            background: "var(--bg-1)",
            border: `1.5px dashed ${dragOver ? "var(--cardinal-bright)" : "var(--line-2)"}`,
            borderRadius: 14,
            padding: "44px 32px 36px",
            textAlign: "center",
            transition: "border-color 120ms",
          }}
        >
          <div
            style={{
              width: 56,
              height: 70,
              margin: "0 auto 22px",
              position: "relative",
              background: "var(--bg-2)",
              border: "1px solid var(--line-2)",
              borderRadius: 4,
            }}
          >
            <div
              style={{
                position: "absolute",
                top: 0,
                right: 0,
                width: 14,
                height: 14,
                background: "var(--bg-0)",
                borderLeft: "1px solid var(--line-2)",
                borderBottom: "1px solid var(--line-2)",
                borderBottomLeftRadius: 2,
              }}
            />
            <div
              style={{
                position: "absolute",
                bottom: 10,
                left: 8,
                right: 8,
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              {[80, 60, 90].map((w, i) => (
                <div
                  key={i}
                  style={{
                    height: 2,
                    width: `${w}%`,
                    background: "var(--line-2)",
                    borderRadius: 1,
                  }}
                />
              ))}
            </div>
          </div>

          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            onChange={onChange}
            style={{ display: "none" }}
          />
          <button
            type="button"
            className="btn btn--primary btn--lg"
            onClick={onClickChoose}
          >
            Choose file
          </button>

          <div
            style={{
              marginTop: 18,
              fontSize: 12,
              color: "var(--ink-3)",
            }}
          >
            PDF only · max 5MB
          </div>
        </div>

        {error && (
          <div
            role="alert"
            style={{
              marginTop: 18,
              fontSize: 13,
              color: "var(--cardinal-bright)",
              fontFamily: "var(--font-sans)",
            }}
          >
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
