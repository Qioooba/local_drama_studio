import { useState } from "react";
import { getProfileVersion, type ProfileVersionDetail } from "../../generated/api";
import { ModelInspectorDrawer } from "./ModelInspectorDrawer";
import "./model-config.css";

/** Shared progressive-disclosure affordance for every Profile selector. */
export function ProfileExecutionDetailButton({ profileVersionId, label = "查看执行详情" }: { profileVersionId?: string | null; label?: string }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [profile, setProfile] = useState<ProfileVersionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const openDetails = () => {
    if (!profileVersionId) return;
    setOpen(true);
    setLoading(true);
    setError(null);
    void getProfileVersion(profileVersionId)
      .then((response) => setProfile(response.profile_version))
      .catch((caught) => setError(String(caught)))
      .finally(() => setLoading(false));
  };
  return <>
    <button type="button" className="secondary profile-detail-button" disabled={!profileVersionId} onClick={openDetails}>
      {loading ? "读取详情中…" : label}
    </button>
    <ModelInspectorDrawer open={open} profile={profile} onClose={() => setOpen(false)} />
    {error && open ? <small className="inline-error" role="alert">读取执行详情失败：{error}</small> : null}
  </>;
}
