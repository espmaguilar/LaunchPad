// Icons + logo from the LabScribe design (Figma Make export), plus small shared components.
import type { ReactNode } from "react";

export type IconName =
  | "live"
  | "file"
  | "records"
  | "users"
  | "settings"
  | "volume"
  | "camera"
  | "chevron"
  | "check"
  | "clock"
  | "spark"
  | "undo"
  | "grip"
  | "edit"
  | "trash"
  | "plus"
  | "upload"
  | "shield"
  | "alert"
  | "download"
  | "share"
  | "more"
  | "play"
  | "mic"
  | "close"
  | "flask"
  | "arrow";

export function Icon({
  name,
  size = 18,
  stroke = 1.8,
}: {
  name: IconName;
  size?: number;
  stroke?: number;
}) {
  const paths: Record<IconName, ReactNode> = {
    live: (
      <>
        <path d="M4 7.5h9a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2Z" />
        <path d="m15 10 4-2v8l-4-2M6 4.5h6" />
      </>
    ),
    file: (
      <>
        <path d="M5 2.5h7l4 4v12H5z" />
        <path d="M12 2.5v4h4M8 11h5M8 14h5" />
      </>
    ),
    records: (
      <>
        <path d="M4 3h12v15H4z" />
        <path d="M7 7h6M7 11h6M7 15h4" />
      </>
    ),
    users: (
      <>
        <path d="M7.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM2 18c.4-3.2 2.2-5 5.5-5s5.1 1.8 5.5 5" />
        <path d="M14 6.3a3 3 0 0 1 0 5.4M14.5 13.2c2.1.6 3.2 2.2 3.5 4.8" />
      </>
    ),
    settings: (
      <>
        <circle cx="10" cy="10" r="3" />
        <path
          d="M16.5 12.5 18 14l-2 3-2-.8-1.3.8-.3 2h-4l-.3-2-1.3-.8-2 .8-2-3 1.5-1.5-.1-1.6-1.8-1V6.5l1.8-1 .1-1.6L2.8 2.5l2-2 2 .8 1.3-.8.3-2h4l.3 2 1.3.8 2-.8 2 2-1.5 1.4.1 1.6 1.8 1v3.4l-1.8 1Z"
          transform="scale(.82) translate(2.2 2.2)"
        />
      </>
    ),
    volume: (
      <>
        <path d="M3 8h3l4-3v10l-4-3H3zM13 7c1.7 1.7 1.7 4.3 0 6M15.5 4.5c3 3 3 8 0 11" />
      </>
    ),
    camera: (
      <>
        <rect x="2.5" y="5" width="15" height="11" rx="2" />
        <path d="m7 5 1-2h4l1 2" />
        <circle cx="10" cy="10.5" r="3" />
      </>
    ),
    chevron: <path d="m7 4 6 6-6 6" />,
    check: <path d="m4 10 4 4 8-9" />,
    clock: (
      <>
        <circle cx="10" cy="10" r="8" />
        <path d="M10 5v5l3 2" />
      </>
    ),
    spark: (
      <>
        <path d="m10 2 1.2 4.2L15 8l-3.8 1.8L10 14l-1.2-4.2L5 8l3.8-1.8Z" />
        <path d="m16 13 .6 2.2L19 16l-2.4.8L16 19l-.6-2.2L13 16l2.4-.8Z" />
      </>
    ),
    undo: (
      <>
        <path d="m7 6-4 4 4 4" />
        <path d="M4 10h7a5 5 0 0 1 5 5v1" />
      </>
    ),
    grip: (
      <>
        <circle cx="7" cy="6" r=".8" fill="currentColor" stroke="none" />
        <circle cx="13" cy="6" r=".8" fill="currentColor" stroke="none" />
        <circle cx="7" cy="10" r=".8" fill="currentColor" stroke="none" />
        <circle cx="13" cy="10" r=".8" fill="currentColor" stroke="none" />
        <circle cx="7" cy="14" r=".8" fill="currentColor" stroke="none" />
        <circle cx="13" cy="14" r=".8" fill="currentColor" stroke="none" />
      </>
    ),
    edit: (
      <>
        <path d="m13.5 3.5 3 3-9.8 9.8-3.7.7.7-3.7Z" />
        <path d="m11.5 5.5 3 3" />
      </>
    ),
    trash: (
      <>
        <path d="M4 6h12M8 3h4l1 3M6 6l1 11h6l1-11M9 9v5M12 9v5" />
      </>
    ),
    plus: <path d="M10 3v14M3 10h14" />,
    upload: (
      <>
        <path d="M10 13V3M6 7l4-4 4 4" />
        <path d="M3 12v5h14v-5" />
      </>
    ),
    shield: (
      <>
        <path d="M10 2 17 5v5c0 4.5-3 7-7 8-4-1-7-3.5-7-8V5Z" />
        <path d="m7 10 2 2 4-5" />
      </>
    ),
    alert: (
      <>
        <path d="M10 2 19 18H1Z" />
        <path d="M10 7v5M10 15h.01" />
      </>
    ),
    download: (
      <>
        <path d="M10 2v11M6 9l4 4 4-4" />
        <path d="M3 16v2h14v-2" />
      </>
    ),
    share: (
      <>
        <circle cx="15" cy="4" r="2" />
        <circle cx="5" cy="10" r="2" />
        <circle cx="15" cy="16" r="2" />
        <path d="m7 9 6-4M7 11l6 4" />
      </>
    ),
    more: (
      <>
        <circle cx="4" cy="10" r="1" fill="currentColor" stroke="none" />
        <circle cx="10" cy="10" r="1" fill="currentColor" stroke="none" />
        <circle cx="16" cy="10" r="1" fill="currentColor" stroke="none" />
      </>
    ),
    play: <path d="m7 4 9 6-9 6Z" />,
    mic: (
      <>
        <rect x="7" y="2" width="6" height="11" rx="3" />
        <path d="M4 10a6 6 0 0 0 12 0M10 16v3" />
      </>
    ),
    close: <path d="M4 4l12 12M16 4 4 16" />,
    flask: (
      <>
        <path d="M7 2h6M8 2v5L3.5 15a2 2 0 0 0 1.8 3h9.4a2 2 0 0 0 1.8-3L12 7V2" />
        <path d="M6 13h8" />
      </>
    ),
    arrow: (
      <>
        <path d="M3 10h14M12 5l5 5-5 5" />
      </>
    ),
  };
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}

export function Logo() {
  return (
    <div className="logo">
      <div className="logo-mark">
        <svg viewBox="0 0 32 32" aria-hidden="true">
          <path
            d="M11 5h10M13 5v7L7.8 22a3 3 0 0 0 2.7 4.4h11a3 3 0 0 0 2.7-4.4L19 12V5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path d="M10 20.5h12" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
          <path d="M13 17h6M14.5 21.5h3" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </div>
      <span>LabScribe</span>
    </div>
  );
}


export function PageHeading({ kicker, title, sub, right }: { kicker: string; title: string; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="page-heading">
      <div>
        <span>{kicker}</span>
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {right}
    </div>
  );
}

export function Notice({ tone, children, onClose }: { tone: "error" | "ok" | "info"; children: ReactNode; onClose?: () => void }) {
  return (
    <div className={`notice ${tone}`} role={tone === "error" ? "alert" : "status"}>
      <div>{children}</div>
      {onClose && <button onClick={onClose} aria-label="Dismiss"><Icon name="close" size={12} /></button>}
    </div>
  );
}
