import type { SVGProps } from "react";

type IconProps = Omit<SVGProps<SVGSVGElement>, "children">;

function iconProps(props: IconProps): IconProps {
  return { "aria-hidden": true, focusable: "false", viewBox: "0 0 20 20", ...props };
}

export function StudioMarkIcon(props: IconProps) {
  return <svg {...iconProps(props)} viewBox="0 0 24 24"><rect x="3.5" y="4" width="17" height="16" rx="3" /><path d="M8 4v16M16 4v16M3.5 9h4.5M16 9h4.5M3.5 15h4.5M16 15h4.5" /></svg>;
}

export function ChevronRightIcon(props: IconProps) {
  return <svg {...iconProps(props)}><path d="m7.5 4.5 5 5.5-5 5.5" /></svg>;
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
