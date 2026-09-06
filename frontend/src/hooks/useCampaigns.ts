import { useCallback, useEffect, useRef, useState } from "react";

import { addCampaignNote, createCampaign, getCampaigns, getPlaybookScripts, patchCampaign, proposeNow } from "../api/playbooks";
import { subscribeReplaySession } from "../api/replayMode";
import type { Campaign, CampaignCreate, CampaignPatch, PlaybookScript } from "../types/playbooks";

const POLL_MS = 15_000;

export interface CampaignsState {
  scripts: PlaybookScript[];
  scriptErrors: { filename: string; error: string }[];
  campaigns: Campaign[];
  loading: boolean;
  error: string | null;
}

export interface CampaignsActions {
  refresh: () => void;
  create: (body: CampaignCreate) => Promise<Campaign>;
  patch: (id: string, body: CampaignPatch) => Promise<Campaign>;
  propose: (id: string) => Promise<Campaign>;
  note: (id: string, note: string) => Promise<Campaign>;
}

const EMPTY: CampaignsState = { scripts: [], scriptErrors: [], campaigns: [], loading: true, error: null };

/**
 * The playbook scripts on offer and the account's campaigns, polled while
 * the Playbooks tab is mounted (the runner refreshes proposals server-side
 * every few minutes and on every event) and refetched on a replay tick.
 * Every action replaces the campaign it returns in place, so the tab does
 * not wait for the next poll to show what it just did.
 */
export function useCampaigns(enabled: boolean, account: "sim" = "sim"): CampaignsState & CampaignsActions {
  const [state, setState] = useState<CampaignsState>(EMPTY);
  const cancelledRef = useRef(false);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    try {
      const [scripts, campaigns] = await Promise.all([getPlaybookScripts(), getCampaigns(account)]);
      if (cancelledRef.current) return;
      setState({ scripts: scripts.playbooks, scriptErrors: scripts.errors, campaigns: campaigns.campaigns, loading: false, error: null });
    } catch (err: unknown) {
      if (cancelledRef.current) return;
      setState((s) => ({ ...s, loading: false, error: err instanceof Error ? err.message : String(err) }));
    }
  }, [enabled, account]);

  useEffect(() => {
    cancelledRef.current = false;
    if (!enabled) {
      setState(EMPTY);
      return;
    }
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    const unsubscribe = subscribeReplaySession(() => void refresh());
    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
      unsubscribe();
    };
  }, [enabled, refresh]);

  const replace = useCallback((campaign: Campaign) => {
    setState((s) => {
      const others = s.campaigns.filter((c) => c.id !== campaign.id);
      const next = campaign.status === "closed" ? others : [campaign, ...others];
      return { ...s, campaigns: next, error: null };
    });
  }, []);

  const create = useCallback(
    async (body: CampaignCreate) => {
      const campaign = await createCampaign(body);
      replace(campaign);
      return campaign;
    },
    [replace],
  );
  const patch = useCallback(
    async (id: string, body: CampaignPatch) => {
      const campaign = await patchCampaign(id, body);
      replace(campaign);
      return campaign;
    },
    [replace],
  );
  const propose = useCallback(
    async (id: string) => {
      const campaign = await proposeNow(id);
      replace(campaign);
      return campaign;
    },
    [replace],
  );
  const note = useCallback(
    async (id: string, text: string) => {
      const campaign = await addCampaignNote(id, text);
      replace(campaign);
      return campaign;
    },
    [replace],
  );

  return { ...state, refresh: () => void refresh(), create, patch, propose, note };
}
