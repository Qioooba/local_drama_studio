import type { SVGProps } from "react";

type IconProps = Omit<SVGProps<SVGSVGElement>, "children">;

export type StudioIconName =
  | "activity" | "assets" | "book" | "check-circle" | "clapperboard"
  | "close" | "cpu" | "export" | "flask" | "grid" | "home" | "magic"
  | "menu" | "more" | "pin" | "play-circle" | "search" | "settings"
  | "shield" | "sidebar" | "sliders" | "sparkles" | "timeline" | "tools" | "waveform"
  | "workflow" | "undo" | "redo";

function iconProps(props: IconProps): IconProps {
  return {
    "aria-hidden": true,
    focusable: "false",
    viewBox: "0 0 20 20",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    ...props,
  };
}

/** Product icon set: one optical size, one stroke language, no font glyphs. */
export function StudioIcon({ name, ...props }: IconProps & { name: StudioIconName }) {
  const common = iconProps(props);
  switch (name) {
    case "activity": return <svg {...common}><path d="M2.5 10h3l1.7-4.5 3.2 9 2.1-6 1.4 3h3.6" /></svg>;
    case "assets": return <svg {...common}><circle cx="7" cy="7" r="2.6" /><circle cx="13.5" cy="8" r="2.1" /><path d="M2.8 16c.5-3 2-4.6 4.3-4.6s3.9 1.6 4.4 4.6M11.4 12c2.8-.6 4.8.9 5.4 3.6" /></svg>;
    case "book": return <svg {...common}><path d="M3 3.5h5.2c1 0 1.8.8 1.8 1.8v11.2c0-1-.8-1.8-1.8-1.8H3zM17 3.5h-5.2c-1 0-1.8.8-1.8 1.8v11.2c0-1 .8-1.8 1.8-1.8H17z" /></svg>;
    case "check-circle": return <svg {...common}><circle cx="10" cy="10" r="7" /><path d="m6.5 10 2.2 2.3 4.8-5" /></svg>;
    case "clapperboard": return <svg {...common}><path d="M3 7h14v9.5H3zM3 7l1.2-4h13L16 7M6 3l-1.2 4M11 3 9.8 7M16 3l-1.2 4" /></svg>;
    case "close": return <svg {...common}><path d="m5 5 10 10M15 5 5 15" /></svg>;
    case "cpu": return <svg {...common}><rect x="5" y="5" width="10" height="10" rx="2" /><rect x="8" y="8" width="4" height="4" rx=".7" /><path d="M7 2.5v2M10 2.5v2M13 2.5v2M7 15.5v2M10 15.5v2M13 15.5v2M2.5 7h2M2.5 10h2M2.5 13h2M15.5 7h2M15.5 10h2M15.5 13h2" /></svg>;
    case "export": return <svg {...common}><path d="M10 13V3M6.5 6.5 10 3l3.5 3.5M4 10v6h12v-6" /></svg>;
    case "flask": return <svg {...common}><path d="M7 3h6M8 3v4l-4.5 7.2A1.8 1.8 0 0 0 5 17h10a1.8 1.8 0 0 0 1.5-2.8L12 7V3M5.6 12h8.8" /></svg>;
    case "grid": return <svg {...common}><rect x="3" y="3" width="5.5" height="5.5" rx="1.2" /><rect x="11.5" y="3" width="5.5" height="5.5" rx="1.2" /><rect x="3" y="11.5" width="5.5" height="5.5" rx="1.2" /><rect x="11.5" y="11.5" width="5.5" height="5.5" rx="1.2" /></svg>;
    case "home": return <svg {...common}><path d="m3 9 7-5.7L17 9v8h-5v-5H8v5H3z" /></svg>;
    case "magic": return <svg {...common}><path d="m4 16 9.8-9.8M11.8 4.2l4 4M5 3v3M3.5 4.5h3M15.5 12v4M13.5 14h4" /></svg>;
    case "menu": return <svg {...common}><path d="M3 5h14M3 10h14M3 15h14" /></svg>;
    case "more": return <svg {...common}><circle cx="4" cy="10" r="1" fill="currentColor" stroke="none" /><circle cx="10" cy="10" r="1" fill="currentColor" stroke="none" /><circle cx="16" cy="10" r="1" fill="currentColor" stroke="none" /></svg>;
    case "pin": return <svg {...common}><path d="M6.5 3.5h7M7.5 3.5v5l-2.5 2.5h10l-2.5-2.5v-5M10 11v6" /></svg>;
    case "play-circle": return <svg {...common}><circle cx="10" cy="10" r="7" /><path d="m8.3 7.2 4.6 2.8-4.6 2.8z" /></svg>;
    case "search": return <svg {...common}><circle cx="8.7" cy="8.7" r="5.5" /><path d="m12.8 12.8 4 4" /></svg>;
    case "settings": return <svg {...common}><circle cx="10" cy="10" r="2.4" /><path d="M16.5 11.5v-3l-2-.7-.6-1.4.9-1.9-2.2-1.2-1.4 1.5H9.7L8.3 3.3 6.1 4.5 7 6.4l-.6 1.4-2 .7v3l2 .7.6 1.4-.9 1.9 2.2 1.2 1.4-1.5h1.5l1.4 1.5 2.2-1.2-.9-1.9.6-1.4z" /></svg>;
    case "shield": return <svg {...common}><path d="M10 2.8 16 5v4.7c0 3.8-2.5 6.3-6 7.5-3.5-1.2-6-3.7-6-7.5V5z" /><path d="m7.2 10 1.8 1.8 3.8-4" /></svg>;
    case "sidebar": return <svg {...common}><rect x="3" y="3.5" width="14" height="13" rx="1.8" /><path d="M7.5 3.5v13" /></svg>;
    case "sliders": return <svg {...common}><path d="M4 3v14M10 3v14M16 3v14M2 7h4M8 13h4M14 8h4" /><circle cx="4" cy="7" r="1.4" fill="currentColor" stroke="none" /><circle cx="10" cy="13" r="1.4" fill="currentColor" stroke="none" /><circle cx="16" cy="8" r="1.4" fill="currentColor" stroke="none" /></svg>;
    case "sparkles": return <svg {...common}><path d="M10 2.5c.5 3.3 2 5 5 5.5-3 .5-4.5 2.2-5 5.5-.5-3.3-2-5-5-5.5 3-.5 4.5-2.2 5-5.5ZM15.5 12.5c.2 1.5.9 2.3 2.2 2.5-1.3.2-2 1-2.2 2.5-.2-1.5-.9-2.3-2.2-2.5 1.3-.2 2-1 2.2-2.5Z" /></svg>;
    case "timeline": return <svg {...common}><path d="M3 5h14M3 10h14M3 15h14M6 3v4M13 8v4M9 13v4" /></svg>;
    case "tools": return <svg {...common}><path d="m11.5 8.5 5 5a2.1 2.1 0 0 1-3 3l-5-5M12 7a4.5 4.5 0 0 0-5.8-4.3L8.8 5.3 5.3 8.8 2.7 6.2A4.5 4.5 0 0 0 7 12c2.5 0 5-2.5 5-5Z" /></svg>;
    case "waveform": return <svg {...common}><path d="M2.5 10h2l1.2-4 2.2 8 2.2-10 2.1 12 1.8-8 1 2h2.5" /></svg>;
    case "workflow": return <svg {...common}><rect x="3" y="3" width="5" height="4" rx="1" /><rect x="12" y="13" width="5" height="4" rx="1" /><rect x="3" y="13" width="5" height="4" rx="1" /><path d="M8 5h3a3 3 0 0 1 3 3v5M5.5 7v6" /></svg>;
    case "undo": return <svg {...common}><path d="M7 5 3 9l4 4" /><path d="M4 9h7a5 5 0 0 1 5 5v1" /></svg>;
    case "redo": return <svg {...common}><path d="m13 5 4 4-4 4" /><path d="M16 9H9a5 5 0 0 0-5 5v1" /></svg>;
  }
}

export function StudioMarkIcon(props: IconProps) {
  return <svg {...iconProps(props)} viewBox="0 0 24 24"><rect x="3.5" y="4" width="17" height="16" rx="3" /><path d="M8 4v16M16 4v16M3.5 9h4.5M16 9h4.5M3.5 15h4.5M16 15h4.5" /></svg>;
}

export function ChevronRightIcon(props: IconProps) {
  return <svg {...iconProps(props)}><path d="m7.5 4.5 5 5.5-5 5.5" /></svg>;
}

export function ChevronLeftIcon(props: IconProps) {
  return <svg {...iconProps(props)}><path d="m12.5 4.5-5 5.5 5 5.5" /></svg>;
}

export function StatusDotIcon(props: IconProps) {
  return <svg {...iconProps(props)}><circle cx="10" cy="10" r="4" fill="currentColor" stroke="none" /></svg>;
}

export function GateStatusIcon({ passed, ...props }: IconProps & { passed: boolean }) {
  return <svg {...iconProps(props)}><circle cx="10" cy="10" r="7" />{passed && <path d="m6.5 10 2.2 2.3 4.8-5" />}</svg>;
}

export function BreadcrumbSeparatorIcon(props: IconProps) {
  return <ChevronRightIcon {...props} className={`breadcrumb-separator ${props.className ?? ""}`.trim()} />;
}
