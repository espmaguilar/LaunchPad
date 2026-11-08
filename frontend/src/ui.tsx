import type { ReactNode } from "react";

const icons: Record<string, ReactNode> = {
  monitor: (
    <>
      <rect x="3" y="4" width="18" height="14" rx="2" />
      <path d="M8 22h8M12 18v4" />
      <path d="m8 11 2.5 2.5L16 8" />
    </>
  ),
  incidents: (
    <>
      <path d="M12 3 2.7 20h18.6L12 3Z" />
      <path d="M12 9v4M12 17h.01" />
    </>
  ),
  dashboard: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="4" rx="1" />
      <rect x="14" y="11" width="7" height="10" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
    </>
  ),
  policy: (
    <>
      <path d="M6 2h9l4 4v16H6z" />
      <path d="M14 2v5h5M9 12h6M9 16h6" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" />
    </>
  ),
  chevron: <path d="m9 18 6-6-6-6" />,
  bell: <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" />,
  sound: <path d="M11 5 6 9H2v6h4l5 4zM15 9a4 4 0 0 1 0 6M18 6a8 8 0 0 1 0 12" />,
  mute: <path d="M11 5 6 9H2v6h4l5 4zM16 9l6 6M22 9l-6 6" />,
  camera: (
    <>
      <path d="M14.5 5 13 3H7L5.5 5H3v15h18V5z" />
      <circle cx="12" cy="12" r="4" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-4-4" />
    </>
  ),
  download: <path d="M12 3v12m0 0 4-4m-4 4-4-4M4 21h16" />,
  more: (
    <>
      <circle cx="5" cy="12" r="1" />
      <circle cx="12" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
    </>
  ),
  edit: <path d="m4 20 4.5-1 10-10-3.5-3.5-10 10zM13 7.5l3.5 3.5" />,
  trash: <path d="M4 7h16M9 3h6l1 4H8zM7 7l1 14h8l1-14" />,
  upload: <path d="M12 16V4m0 0L7 9m5-5 5 5M4 20h16" />,
  plus: <path d="M12 5v14M5 12h14" />,
  check: <path d="m5 12 4 4L19 6" />,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  file: (
    <>
      <path d="M6 2h9l4 4v16H6z" />
      <path d="M14 2v5h5M9 12h6M9 16h4" />
    </>
  ),
  arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
};

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {icons[name]}
    </svg>
  );
}

export function Logo() {
  return (
    <div className="logo">
      <svg width="32" height="32" viewBox="0 0 32 32" aria-hidden>
        <path d="M16 2.5 27 6v8.5c0 7.2-4.6 12-11 15-6.4-3-11-7.8-11-15V6z" fill="#FFB020" />
        <path d="M8.8 15.6s2.7-4 7.2-4 7.2 4 7.2 4-2.7 4-7.2 4-7.2-4-7.2-4Z" fill="#0B0D10" />
        <circle cx="16" cy="15.6" r="2.2" fill="#FFB020" />
      </svg>
      <span>SafeSight</span>
    </div>
  );
}

export function PageTitle({ title, subtitle, action }: { title: string; subtitle: ReactNode; action?: ReactNode }) {
  return (
    <div className="page-title">
      <div>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      {action}
    </div>
  );
}

export function Status({ value }: { value: string }) {
  return (
    <span className={`status ${value.toLowerCase().replace(" ", "-")}`}>
      <i />
      {value}
    </span>
  );
}

export function DetectorTag({ detector }: { detector?: string }) {
  const ai = detector === "AI vision";
  return <span className={`det-tag ${ai ? "ai" : "rek"}`}>{ai ? "AI vision" : "Rekognition"}</span>;
}

export function Banner({ tone, children, onClose }: { tone: "error" | "ok" | "info"; children: ReactNode; onClose?: () => void }) {
  return (
    <div className={`banner ${tone}`} role={tone === "error" ? "alert" : "status"}>
      <div>{children}</div>
      {onClose && (
        <button onClick={onClose} aria-label="Dismiss">
          <Icon name="close" size={14} />
        </button>
      )}
    </div>
  );
}
