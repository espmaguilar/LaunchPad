// Icons (Lucide) + logo from the LabScribe design, plus small shared components.
import {
  ArrowRight, Camera, Check, ChevronRight, ClipboardList, Clock, Download, Ellipsis, FileText,
  FlaskConical, GripVertical, Mic, Pencil, Play, Plus, RotateCcw, Settings, Share2, ShieldCheck,
  Sparkles, Trash2, TriangleAlert, Undo2, Upload, Users, Video, Volume2, X, type LucideIcon,
} from "lucide-react";
import { useEffect as useEffectLB, useState as useStateLB, type ReactNode } from "react";

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
  | "restart"
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

const ICONS: Record<IconName, LucideIcon> = {
  live: Video,
  file: FileText,
  records: ClipboardList,
  users: Users,
  settings: Settings,
  volume: Volume2,
  camera: Camera,
  chevron: ChevronRight,
  check: Check,
  clock: Clock,
  spark: Sparkles,
  undo: Undo2,
  restart: RotateCcw,
  grip: GripVertical,
  edit: Pencil,
  trash: Trash2,
  plus: Plus,
  upload: Upload,
  shield: ShieldCheck,
  alert: TriangleAlert,
  download: Download,
  share: Share2,
  more: Ellipsis,
  play: Play,
  mic: Mic,
  close: X,
  flask: FlaskConical,
  arrow: ArrowRight,
};

export function Icon({
  name,
  size = 18,
  stroke = 1.8,
}: {
  name: IconName;
  size?: number;
  stroke?: number;
}) {
  const Cmp = ICONS[name];
  return <Cmp aria-hidden="true" size={size} strokeWidth={stroke} />;
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

// ---------- image carousel (lightbox) ----------

export interface Slide {
  src: string;
  title: string;
  caption?: string;
}

/** Full-screen image carousel: arrows / keyboard ← → to browse, Esc or backdrop to close. */
export function Lightbox({ slides, index, onClose }: { slides: Slide[]; index: number; onClose: () => void }) {
  const [i, setI] = useStateLB(index);
  const n = slides.length;
  useEffectLB(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") setI((x) => (x + 1) % n);
      if (e.key === "ArrowLeft") setI((x) => (x - 1 + n) % n);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [n, onClose]);
  if (!n) return null;
  const s = slides[i];
  return (
    <div className="lightbox" role="dialog" aria-modal="true" aria-label="Snapshot viewer" onClick={onClose}>
      <div className="lightbox-inner" onClick={(e) => e.stopPropagation()}>
        <button className="lb-close" onClick={onClose} aria-label="Close"><Icon name="close" size={16} /></button>
        {n > 1 && <button className="lb-nav prev" onClick={() => setI((i - 1 + n) % n)} aria-label="Previous"><Icon name="chevron" size={22} /></button>}
        <img src={s.src} alt={s.title} />
        {n > 1 && <button className="lb-nav next" onClick={() => setI((i + 1) % n)} aria-label="Next"><Icon name="chevron" size={22} /></button>}
        <div className="lb-caption">
          <strong>{s.title}</strong>
          {s.caption && <span>{s.caption}</span>}
          {n > 1 && <em>{i + 1} / {n}</em>}
        </div>
        {n > 1 && (
          <div className="lb-thumbs">
            {slides.map((t, k) => (
              <button key={t.src + k} className={k === i ? "on" : ""} onClick={() => setI(k)} aria-label={t.title}>
                <img src={t.src} alt="" />
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
