type Props = {
  currentIndex: number;
  count: number;
  onChange: (idx: number) => void;
};

export function VersionNavigator({ currentIndex, count, onChange }: Props) {
  if (count <= 1) return null;

  const atStart = currentIndex <= 0;
  const atEnd = currentIndex >= count - 1;

  const arrowStyle = (disabled: boolean): React.CSSProperties => ({
    width: 28,
    height: 28,
    borderRadius: 6,
    background: "transparent",
    border: "1px solid var(--line-2)",
    color: disabled ? "var(--ink-4)" : "var(--ink-1)",
    cursor: disabled ? "default" : "pointer",
    display: "grid",
    placeItems: "center",
    fontSize: 15,
  });

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        flexShrink: 0,
        paddingTop: 28,
      }}
    >
      <button
        type="button"
        onClick={() => !atStart && onChange(currentIndex - 1)}
        disabled={atStart}
        style={arrowStyle(atStart)}
        aria-label="Previous version"
      >
        ‹
      </button>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--ink-2)",
          minWidth: 28,
          textAlign: "center",
        }}
      >
        {currentIndex + 1} / {count}
      </span>
      <button
        type="button"
        onClick={() => !atEnd && onChange(currentIndex + 1)}
        disabled={atEnd}
        style={arrowStyle(atEnd)}
        aria-label="Next version"
      >
        ›
      </button>
    </div>
  );
}
