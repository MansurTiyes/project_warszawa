type Props = {
  remarks: string[];
};

export function RemarksList({ remarks }: Props) {
  if (remarks.length === 0) return null;
  return (
    <div>
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--ink-3)",
          textTransform: "uppercase",
          letterSpacing: "0.1em",
          marginBottom: 14,
        }}
      >
        Remarks
      </div>
      <ul
        style={{
          margin: 0,
          paddingLeft: 18,
          fontSize: 14,
          lineHeight: 1.6,
          color: "var(--ink-1)",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        {remarks.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>
    </div>
  );
}
